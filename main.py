import uvicorn
from fastapi import FastAPI, HTTPException, Query
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import time
import uuid
import os

app = FastAPI()

# --- Global Session Storage ---
SESSION = {
    "cookies": None,
    "user_agent": None,
    "nonce": None,
    "bot_id": "25874",  # Gemini ID (Change to 25873 for DeepSeek if needed)
    "last_updated": 0
}

def refresh_session():
    """Headless browser chala kar nayi Cookies aur Nonce layega"""
    print("🔄 Refreshing Session via Playwright...")
    try:
        with sync_playwright() as p:
            # Browser Launch (Headless)
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36"
            )
            page = context.new_page()

            # 1. Load Website
            page.goto("https://chatgptfree.ai/chat/", timeout=60000)
            
            # 2. Wait for Chat Container (Cloudflare check pass hone ka wait)
            # Hum Gemini bot ID ka wait karenge
            selector = f"#aipkit_chat_container_{SESSION['bot_id']}"
            page.wait_for_selector(selector, timeout=60000)

            # 3. Extract Data (Nonce)
            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')
            chat_div = soup.find("div", {"id": f"aipkit_chat_container_{SESSION['bot_id']}"})
            
            if not chat_div:
                raise Exception("Chat container not found!")

            config_data = json.loads(chat_div['data-config'])
            nonce = config_data.get('nonce')

            # 4. Extract Cookies
            cookies = context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}

            # Update Global Session
            SESSION["cookies"] = cookie_dict
            SESSION["user_agent"] = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36"
            SESSION["nonce"] = nonce
            SESSION["last_updated"] = time.time()
            
            print(f"✅ Session Refreshed! Nonce: {nonce}")
            browser.close()
            return True
    except Exception as e:
        print(f"❌ Failed to refresh session: {e}")
        return False

def make_api_request(message):
    """Ye function pehle POST karega phir GET stream karega"""
    
    url = "https://chatgptfree.ai/wp-admin/admin-ajax.php"
    
    # Unique IDs generate karna zaroori hai
    client_msg_id = f"aipkit-client-msg-{SESSION['bot_id']}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"
    
    headers = {
        "User-Agent": SESSION["user_agent"],
        "Origin": "https://chatgptfree.ai",
        "Referer": "https://chatgptfree.ai/chat/",
        "X-Requested-With": "XMLHttpRequest"
    }

    # --- STEP 1: POST Request (To get Cache Key) ---
    # Multipart form data bhejna zaroori hai
    files = {
        'action': (None, 'aipkit_cache_sse_message'),
        'message': (None, message),
        '_ajax_nonce': (None, SESSION["nonce"]),
        'bot_id': (None, SESSION["bot_id"]),
        'user_client_message_id': (None, client_msg_id)
    }

    try:
        print("🚀 Sending Step 1 (POST)...")
        post_response = requests.post(url, cookies=SESSION["cookies"], headers=headers, files=files, timeout=10)
        
        if post_response.status_code != 200:
            raise Exception(f"POST Error: {post_response.status_code}")
            
        post_json = post_response.json()
        if not post_json.get("success"):
            raise Exception("POST Success False - Cookies might be expired")
            
        cache_key = post_json["data"]["cache_key"]
        print(f"✅ Got Cache Key: {cache_key}")

        # --- STEP 2: GET Request (Streaming Response) ---
        params = {
            "action": "aipkit_frontend_chat_stream",
            "cache_key": cache_key,
            "bot_id": SESSION["bot_id"],
            "session_id": str(uuid.uuid4()), # Random Session ID
            "conversation_uuid": str(uuid.uuid4()), # Random Conversation ID
            "post_id": "261",
            "_ts": int(time.time()*1000),
            "_ajax_nonce": SESSION["nonce"]
        }

        print("📡 Sending Step 2 (GET Stream)...")
        stream_response = requests.get(url, cookies=SESSION["cookies"], headers=headers, params=params, stream=True, timeout=20)

        full_reply = ""
        
        # Stream ko process karna (Chunk by Chunk)
        for line in stream_response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data:"):
                    json_str = decoded_line.replace("data: ", "")
                    try:
                        data_chunk = json.loads(json_str)
                        # Agar delta (text) ho to add karo
                        if "delta" in data_chunk:
                            full_reply += data_chunk["delta"]
                        # Agar finished flag ho to break karo
                        if data_chunk.get("finished", False) is True:
                            break
                    except:
                        pass
        
        if not full_reply:
            raise Exception("Empty Response from AI")

        return full_reply

    except Exception as e:
        print(f"⚠️ Error in API Request: {e}")
        raise e # Error wapis bhejo taake retry logic handle kare

@app.get("/chat")
def chat_endpoint(message: str = Query(..., description="User message")):
    # Agar session khali hai to pehli baar load karo
    if not SESSION["cookies"]:
        success = refresh_session()
        if not success:
            return {"error": "Failed to initialize session"}

    try:
        # Koshish karo message bhejne ki
        reply = make_api_request(message)
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        print("🔄 Token Expired? Retrying with fresh session...")
        # Agar fail ho jaye to session refresh karke dubara try karo
        refresh_session()
        try:
            reply = make_api_request(message)
            return {"response": reply, "status": "success"}
        except Exception as final_e:
            return {"error": str(final_e), "status": "failed"}

# Railway Requirement: Port handle karna
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
