import uvicorn
from fastapi import FastAPI, Query
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import time
import uuid
import os

app = FastAPI()

# --- Global Session ---
SESSION = {
    "browser": None,
    "context": None,
    "page": None,
    "nonce": None,
    "bot_id": "25874",  # Gemini ID
    "last_activity": 0
}

def log(text):
    print(f"[LOG] {text}", flush=True)

def init_browser():
    """Browser ko start karke memory me rakhega"""
    if SESSION["page"] and not SESSION["page"].is_closed():
        return True

    log("🔵 INITIALIZING HEAVY BROWSER SESSION...")
    try:
        p = sync_playwright().start()
        
        # Heavy Browser Launch
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage" # Memory usage optimize karne ke liye
            ]
        )
        
        # Context with Real User Agent
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        
        log("   👉 Loading Website...")
        page.goto("https://chatgptfree.ai/chat/", timeout=90000, wait_until="domcontentloaded")
        
        # Wait for Cloudflare & Chat Element
        selector = f"#aipkit_chat_container_{SESSION['bot_id']}"
        try:
            page.wait_for_selector(selector, state="attached", timeout=60000)
            log("   ✅ Website Loaded Successfully!")
        except:
            log("   ❌ Failed to find chat container. Cloudflare might be looping.")
            # Page reload try
            page.reload()
            page.wait_for_selector(selector, state="attached", timeout=60000)

        # Extract Nonce
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        chat_div = soup.find("div", {"id": f"aipkit_chat_container_{SESSION['bot_id']}"})
        config_data = json.loads(chat_div['data-config'])
        SESSION["nonce"] = config_data.get('nonce')
        
        log(f"   🔑 NONCE SECURED: {SESSION['nonce']}")

        # Save objects to global session
        SESSION["playwright"] = p
        SESSION["browser"] = browser
        SESSION["context"] = context
        SESSION["page"] = page
        SESSION["last_activity"] = time.time()
        
        return True

    except Exception as e:
        log(f"❌ CRITICAL INIT ERROR: {e}")
        return False

def run_js_fetch(message):
    """
    Ye function Python Requests use nahi karega.
    Ye Browser ke andar JS run karke data layega.
    """
    page = SESSION["page"]
    nonce = SESSION["nonce"]
    bot_id = SESSION["bot_id"]
    client_msg_id = f"aipkit-client-msg-{bot_id}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"

    # --- STEP 1: JS Injection for POST ---
    # Hum browser se kehte hain ke form data bana kar POST request bhejo
    log("🚀 EXECUTING JS: Fetch POST (Inside Browser)...")
    
    post_script = """
    async (args) => {
        const formData = new FormData();
        formData.append('action', 'aipkit_cache_sse_message');
        formData.append('message', args.message);
        formData.append('_ajax_nonce', args.nonce);
        formData.append('bot_id', args.bot_id);
        formData.append('user_client_message_id', args.client_id);

        try {
            const response = await fetch('https://chatgptfree.ai/wp-admin/admin-ajax.php', {
                method: 'POST',
                body: formData
            });
            return await response.json();
        } catch (e) {
            return { error: e.toString() };
        }
    }
    """
    
    post_result = page.evaluate(post_script, {
        "message": message,
        "nonce": nonce,
        "bot_id": bot_id,
        "client_id": client_msg_id
    })

    log(f"   👉 JS POST Result: {post_result}")

    if not post_result or "success" not in post_result or not post_result["success"]:
        raise Exception(f"Browser POST Failed: {post_result}")

    cache_key = post_result["data"]["cache_key"]
    log(f"   ✅ CACHE KEY: {cache_key}")

    # --- STEP 2: JS Injection for GET STREAM ---
    # Hum browser se kehte hain ke stream read kare aur text wapis kare
    log("📡 EXECUTING JS: Fetch GET Stream (Inside Browser)...")

    stream_script = """
    async (args) => {
        const params = new URLSearchParams({
            "action": "aipkit_frontend_chat_stream",
            "cache_key": args.cache_key,
            "bot_id": args.bot_id,
            "session_id": args.uuid,
            "conversation_uuid": args.uuid,
            "post_id": "261",
            "_ts": Date.now(),
            "_ajax_nonce": args.nonce
        });

        const response = await fetch(`https://chatgptfree.ai/wp-admin/admin-ajax.php?${params.toString()}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, {stream: true});
            
            // Raw text ko parse karna
            const lines = chunk.split('\\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const jsonStr = line.replace('data: ', '');
                        const data = JSON.parse(jsonStr);
                        if (data.delta) fullText += data.delta;
                    } catch (e) {}
                }
            }
        }
        return fullText;
    }
    """

    final_response = page.evaluate(stream_script, {
        "cache_key": cache_key,
        "bot_id": bot_id,
        "uuid": str(uuid.uuid4()),
        "nonce": nonce
    })

    if not final_response:
        raise Exception("Empty response from Browser Stream")

    log(f"🎉 FINAL RESPONSE LENGTH: {len(final_response)}")
    return final_response

@app.get("/chat")
def chat_endpoint(message: str = Query(..., description="User message")):
    log(f"📨 REQUEST: {message}")
    
    # Ensure Browser is alive
    if not SESSION["page"] or SESSION["page"].is_closed():
        success = init_browser()
        if not success:
            return {"error": "Browser Init Failed", "status": "failed"}

    try:
        # Browser ke andar hi request chalayen
        reply = run_js_fetch(message)
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        log(f"⚠️ Error detected: {e}. Restarting Browser...")
        # Agar error aye to browser restart karo
        try:
            SESSION["browser"].close()
        except:
            pass
        
        init_browser()
        try:
            log("🔄 Retrying request inside new browser session...")
            reply = run_js_fetch(message)
            return {"response": reply, "status": "success"}
        except Exception as final_e:
            return {"error": str(final_e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Browser ko start me hi load kar lo
    try:
        init_browser()
    except:
        pass
    uvicorn.run(app, host="0.0.0.0", port=port)
