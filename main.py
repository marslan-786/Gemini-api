import uvicorn
from fastapi import FastAPI, Query
from playwright.async_api import async_playwright
from contextlib import asynccontextmanager
import json
import time
import uuid
import os
import asyncio

# --- Global State ---
class BrowserSession:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.bot_id = "25874" # Gemini ID

session = BrowserSession()

def log(text):
    print(f"[LOG] {text}", flush=True)

# --- Startup & Shutdown Logic ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Browser Launch karo
    log("🔵 STARTING ASYNC BROWSER SESSION...")
    session.playwright = await async_playwright().start()
    
    log("   👉 Launching Chromium (Headless)...")
    session.browser = await session.playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"
        ]
    )
    
    session.context = await session.browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    
    session.page = await session.context.new_page()
    
    log("   👉 Loading https://chatgptfree.ai/chat/ ...")
    await session.page.goto("https://chatgptfree.ai/chat/", timeout=120000, wait_until="domcontentloaded")
    
    # Cloudflare Check
    selector = f"#aipkit_chat_container_{session.bot_id}"
    try:
        log(f"   ⏳ Waiting for selector: {selector}...")
        await session.page.wait_for_selector(selector, state="attached", timeout=60000)
        log("   ✅ Website Loaded & Cloudflare Passed!")
    except Exception as e:
        log(f"   ❌ Timeout waiting for selector. HTML dump below:")
        content = await session.page.content()
        log(content[:500]) # First 500 chars
    
    yield # Yahan app chalegi
    
    # Shutdown: Browser band karo
    log("🔴 SHUTTING DOWN BROWSER...")
    await session.browser.close()
    await session.playwright.stop()

app = FastAPI(lifespan=lifespan)

# --- Browser-Native Fetch Logic ---
async def execute_browser_request(message):
    """
    Ye function Python se request nahi bhejta.
    Ye Browser ke andar JS inject karta hai jo wahan se fetch karti hai.
    Is se Cloudflare 403 nahi de sakta kyunke request browser ke andar se ja rahi hai.
    """
    
    # 1. Get Nonce from Page (Fresh every time)
    log("   👉 Extracting Nonce from Page...")
    nonce_script = f"""
    () => {{
        const div = document.querySelector('#aipkit_chat_container_{session.bot_id}');
        if (!div) return null;
        const config = JSON.parse(div.getAttribute('data-config'));
        return config.nonce;
    }}
    """
    nonce = await session.page.evaluate(nonce_script)
    if not nonce:
        raise Exception("Nonce not found on page")
    log(f"   🔑 Nonce: {nonce}")

    client_msg_id = f"aipkit-client-msg-{session.bot_id}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"

    # 2. JS Injection: POST & GET Stream Handler
    # Hum poora logic JS me likh kar browser ko denge
    log("🚀 EXECUTING JS FETCH INSIDE BROWSER...")
    
    js_logic = """
    async ({ message, nonce, bot_id, client_id }) => {
        // --- STEP 1: POST ---
        const formData = new FormData();
        formData.append('action', 'aipkit_cache_sse_message');
        formData.append('message', message);
        formData.append('_ajax_nonce', nonce);
        formData.append('bot_id', bot_id);
        formData.append('user_client_message_id', client_id);

        const postResp = await fetch('https://chatgptfree.ai/wp-admin/admin-ajax.php', {
            method: 'POST',
            body: formData
        });
        
        const postJson = await postResp.json();
        if (!postJson.success) return { error: "POST Failed", details: postJson };
        
        const cacheKey = postJson.data.cache_key;

        // --- STEP 2: GET STREAM ---
        const params = new URLSearchParams({
            "action": "aipkit_frontend_chat_stream",
            "cache_key": cacheKey,
            "bot_id": bot_id,
            "session_id": "random-sess-" + Date.now(),
            "conversation_uuid": "random-conv-" + Date.now(),
            "post_id": "261",
            "_ts": Date.now(),
            "_ajax_nonce": nonce
        });

        const streamResp = await fetch(`https://chatgptfree.ai/wp-admin/admin-ajax.php?${params.toString()}`);
        const reader = streamResp.body.getReader();
        const decoder = new TextDecoder();
        let finalReply = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, {stream: true});
            const lines = chunk.split('\\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const jsonStr = line.replace('data: ', '');
                        const data = JSON.parse(jsonStr);
                        if (data.delta) finalReply += data.delta;
                    } catch (e) {}
                }
            }
        }
        return { success: true, reply: finalReply };
    }
    """

    # Execute JS
    result = await session.page.evaluate(js_logic, {
        "message": message,
        "nonce": nonce,
        "bot_id": session.bot_id,
        "client_id": client_msg_id
    })

    if "error" in result:
        raise Exception(f"JS Error: {result}")
    
    return result["reply"]

@app.get("/chat")
async def chat_endpoint(message: str = Query(..., description="User message")):
    log(f"📨 REQUEST: {message}")
    
    # Agar browser band ho to restart karo
    if not session.page or session.page.is_closed():
        log("⚠️ Browser was closed. Restarting...")
        # (Yahan reload logic simple rakha hai, ideally lifespan restart hona chahiye)
        # For simplicity, we assume browser stays open via lifespan.
        return {"error": "Browser crashed, please redeploy or restart container."}

    try:
        reply = await execute_browser_request(message)
        log(f"🎉 RESPONSE LENGTH: {len(reply)}")
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        log(f"❌ ERROR: {e}")
        # Agar page crash ho gya ho to reload kar lo
        try:
            log("🔄 Reloading Page...")
            await session.page.reload()
        except:
            pass
        return {"error": str(e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
