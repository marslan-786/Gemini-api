import uvicorn
from fastapi import FastAPI, Query
from curl_cffi import requests  # Special library to bypass Cloudflare
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import time
import uuid
import os

app = FastAPI()

# --- Global Session ---
SESSION = {
    "cookies": None,
    "user_agent": None,
    "nonce": None,
    "bot_id": "25874",  # Gemini ID
    "last_updated": 0
}

def log(text):
    print(f"[LOG] {text}", flush=True)

def refresh_session():
    """Headless browser to extract Cookies & Nonce"""
    log("🔵 STARTING BROWSER SESSION REFRESH...")
    try:
        with sync_playwright() as p:
            log("   👉 Launching Browser...")
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            log("   👉 Navigating to Chat Page...")
            page.goto("https://chatgptfree.ai/chat/", timeout=90000, wait_until="domcontentloaded")
            
            # Selector ka wait (Hidden ho ya Visible)
            selector = f"#aipkit_chat_container_{SESSION['bot_id']}"
            try:
                page.wait_for_selector(selector, state="attached", timeout=40000)
                log("   ✅ Chat Element Found!")
                
                # Thora wait taake Cloudflare cookies set ho jayen
                log("   ⏳ Waiting 5 seconds for cookies to settle...")
                time.sleep(5)
                
            except Exception as e:
                log(f"   ❌ Selector Timeout: {e}")
                browser.close()
                return False

            # HTML Parsing
            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')
            chat_div = soup.find("div", {"id": f"aipkit_chat_container_{SESSION['bot_id']}"})
            
            if not chat_div:
                log("   ❌ Chat DIV not found in HTML.")
                browser.close()
                return False

            # Nonce Extraction
            try:
                config_raw = chat_div.get('data-config')
                config_data = json.loads(config_raw)
                nonce = config_data.get('nonce')
                log(f"   🔑 NONCE FOUND: {nonce}")
            except:
                log("   ❌ Failed to parse JSON config.")
                browser.close()
                return False

            # Cookies Extraction
            cookies = context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            log(f"   🍪 COOKIES EXTRACTED: {len(cookie_dict)}") # Yahan 5-10 cookies honi chahiyen

            # Update Session
            SESSION["cookies"] = cookie_dict
            SESSION["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            SESSION["nonce"] = nonce
            SESSION["last_updated"] = time.time()
            
            browser.close()
            return True

    except Exception as e:
        log(f"❌ CRITICAL ERROR: {str(e)}")
        return False

def make_api_request(message):
    url = "https://chatgptfree.ai/wp-admin/admin-ajax.php"
    client_msg_id = f"aipkit-client-msg-{SESSION['bot_id']}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"
    
    # Headers wohi jo browser ne use kiye
    headers = {
        "User-Agent": SESSION["user_agent"],
        "Origin": "https://chatgptfree.ai",
        "Referer": "https://chatgptfree.ai/chat/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*"
    }

    # Multipart Data exactly jaisa tumne bheja
    data = {
        "action": "aipkit_cache_sse_message",
        "message": message,
        "_ajax_nonce": SESSION["nonce"],
        "bot_id": SESSION["bot_id"],
        "user_client_message_id": client_msg_id
    }

    log("🚀 SENDING POST REQUEST (Using curl_cffi to bypass 403)...")
    
    try:
        # Yahan 'impersonate="chrome"' ka jadoo chalega
        post_response = requests.post(
            url, 
            data=data, 
            cookies=SESSION["cookies"], 
            headers=headers, 
            impersonate="chrome124",  # Real Browser Fingerprint
            timeout=15
        )
        
        log(f"   👉 POST Status: {post_response.status_code}")
        
        if post_response.status_code != 200:
            log(f"   ❌ POST Failed Text: {post_response.text}")
            raise Exception(f"POST Error {post_response.status_code}")

        post_json = post_response.json()
        if not post_json.get("success"):
            raise Exception("POST Success False")
            
        cache_key = post_json["data"]["cache_key"]
        log(f"   ✅ CACHE KEY: {cache_key}")

        # --- STEP 2: STREAMING GET ---
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

        log("📡 STARTING STREAM...")
        stream_response = requests.get(
            url, 
            cookies=SESSION["cookies"], 
            headers=headers, 
            params=params, 
            impersonate="chrome124", 
            stream=True, 
            timeout=30
        )

        full_reply = ""
        
        # Curl_cffi stream handling
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
            raise Exception("Empty Reply")

        log(f"🎉 SUCCESS! Response Length: {len(full_reply)}")
        return full_reply

    except Exception as e:
        log(f"⚠️ API ERROR: {e}")
        raise e 

@app.get("/chat")
def chat_endpoint(message: str = Query(..., description="User message")):
    log(f"📨 REQUEST: {message}")
    
    if not SESSION["cookies"]:
        refresh_session()

    try:
        reply = make_api_request(message)
        return {"response": reply, "status": "success"}
    except:
        log("🔄 Retrying with fresh cookies...")
        refresh_session()
        try:
            reply = make_api_request(message)
            return {"response": reply, "status": "success"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
