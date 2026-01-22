import uvicorn
from fastapi import FastAPI, Query
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import time
import uuid
import os
import sys

app = FastAPI()

# --- Global Session Storage ---
SESSION = {
    "cookies": None,
    "user_agent": None,
    "nonce": None,
    "bot_id": "25874",  # Gemini ID
    "last_updated": 0
}

def log(text):
    """Logs print karne ke liye helper function"""
    print(f"[LOG] {text}", flush=True)

def refresh_session():
    """Headless browser chala kar nayi Cookies aur Nonce layega"""
    log("🔵 STARTING BROWSER SESSION REFRESH...")
    try:
        with sync_playwright() as p:
            log("   👉 Launching Chromium Browser...")
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36"
            )
            page = context.new_page()

            log("   👉 Navigating to URL...")
            try:
                response = page.goto("https://chatgptfree.ai/chat/", timeout=60000, wait_until="domcontentloaded")
                log(f"   📄 Page Status Code: {response.status if response else 'Unknown'}")
                log(f"   📄 Page Title: {page.title()}")
            except Exception as e:
                log(f"   ❌ Navigation Error: {str(e)}")
                # Agar timeout ho jaye tab bhi html check karo, shayad load ho chuka ho
            
            # Selector Definition
            selector = f"#aipkit_chat_container_{SESSION['bot_id']}"
            log(f"   👉 Looking for selector: {selector} in DOM (Hidden or Visible)...")
            
            try:
                # YAHAN CHANGE KIA HAI: state="attached" (Hidden element ko bhi dhoond le ga)
                page.wait_for_selector(selector, state="attached", timeout=30000)
                log("   ✅ Target Element Found in DOM!")
            except Exception as e:
                log(f"   ❌ Selector Timeout! Dumping HTML snippet for debugging...")
                content = page.content()
                log(f"   📜 HTML DUMP (First 1000 chars): {content[:1000]}")
                log(f"   📜 HTML DUMP (Last 1000 chars): {content[-1000:]}")
                browser.close()
                return False

            # HTML Extract
            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find Div specifically
            chat_div = soup.find("div", {"id": f"aipkit_chat_container_{SESSION['bot_id']}"})
            
            if not chat_div:
                log("   ❌ Soup failed to find div, even though Playwright saw it.")
                browser.close()
                return False

            log("   👉 Extracting 'data-config' attribute...")
            try:
                config_raw = chat_div.get('data-config')
                log(f"   📄 Raw Config Data Found: {config_raw[:100]}...") # Sirf shuru ka hissa print karega
                config_data = json.loads(config_raw)
                nonce = config_data.get('nonce')
                
                if not nonce:
                    log("   ❌ Nonce is missing in data-config!")
                    browser.close()
                    return False
                    
                log(f"   🔑 NONCE EXTRACTED: {nonce}")
            except Exception as e:
                log(f"   ❌ JSON Parsing Error: {e}")
                browser.close()
                return False

            # Extract Cookies
            cookies = context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            log(f"   🍪 COOKIES EXTRACTED: {len(cookie_dict)} cookies found.")

            # Update Global Session
            SESSION["cookies"] = cookie_dict
            SESSION["user_agent"] = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36"
            SESSION["nonce"] = nonce
            SESSION["last_updated"] = time.time()
            
            browser.close()
            log("🟢 SESSION REFRESH SUCCESSFUL!")
            return True

    except Exception as e:
        log(f"❌ CRITICAL ERROR in refresh_session: {str(e)}")
        return False

def make_api_request(message):
    """Ye function pehle POST karega phir GET stream karega"""
    
    url = "https://chatgptfree.ai/wp-admin/admin-ajax.php"
    client_msg_id = f"aipkit-client-msg-{SESSION['bot_id']}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"
    
    headers = {
        "User-Agent": SESSION["user_agent"],
        "Origin": "https://chatgptfree.ai",
        "Referer": "https://chatgptfree.ai/chat/",
        "X-Requested-With": "XMLHttpRequest"
    }

    files = {
        'action': (None, 'aipkit_cache_sse_message'),
        'message': (None, message),
        '_ajax_nonce': (None, SESSION["nonce"]),
        'bot_id': (None, SESSION["bot_id"]),
        'user_client_message_id': (None, client_msg_id)
    }

    log("🚀 SENDING POST REQUEST (Step 1)...")
    try:
        post_response = requests.post(url, cookies=SESSION["cookies"], headers=headers, files=files, timeout=15)
        log(f"   👉 POST Status Code: {post_response.status_code}")
        log(f"   👉 POST Response Text: {post_response.text}") # Raw Response check karo

        if post_response.status_code != 200:
            raise Exception(f"POST Error: {post_response.status_code}")
            
        post_json = post_response.json()
        if not post_json.get("success"):
            log("   ❌ POST Success is False.")
            raise Exception("POST Success False")
            
        cache_key = post_json["data"]["cache_key"]
        log(f"   ✅ GOT CACHE KEY: {cache_key}")

        # --- STEP 2: GET Request ---
        params = {
            "action": "aipkit_frontend_chat_stream",
            "cache_key": cache_key,
            "bot_id": SESSION["bot_id"],
            "session_id": str(uuid.uuid4()),
            "conversation_uuid": str(uuid.uuid4()),
            "post_id": "261",
            "_ts": int(time.time()*1000),
            "_ajax_nonce": SESSION["nonce"]
        }

        log("📡 SENDING GET STREAM REQUEST (Step 2)...")
        stream_response = requests.get(url, cookies=SESSION["cookies"], headers=headers, params=params, stream=True, timeout=20)
        
        full_reply = ""
        for line in stream_response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data:"):
                    json_str = decoded_line.replace("data: ", "")
                    try:
                        data_chunk = json.loads(json_str)
                        if "delta" in data_chunk:
                            full_reply += data_chunk["delta"]
                        if data_chunk.get("finished", False) is True:
                            break
                    except:
                        pass
        
        if not full_reply:
            log("   ❌ Stream finished but response is EMPTY.")
            raise Exception("Empty Response from AI")

        log(f"🎉 FINAL RESPONSE LENGTH: {len(full_reply)} chars")
        return full_reply

    except Exception as e:
        log(f"⚠️ API REQUEST ERROR: {e}")
        raise e 

@app.get("/chat")
def chat_endpoint(message: str = Query(..., description="User message")):
    log(f"📨 NEW USER REQUEST: message='{message}'")
    
    if not SESSION["cookies"]:
        log("⚠️ No session found. Initializing first login...")
        success = refresh_session()
        if not success:
            return {"error": "Failed to initialize session", "status": "failed"}

    try:
        reply = make_api_request(message)
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        log("⚠️ Request failed. Retrying with fresh session...")
        refresh_success = refresh_session()
        if not refresh_success:
             return {"error": "Failed to refresh session", "status": "failed"}
             
        try:
            reply = make_api_request(message)
            return {"response": reply, "status": "success"}
        except Exception as final_e:
            return {"error": str(final_e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
