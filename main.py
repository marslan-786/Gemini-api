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
    log("🔵 STARTING STEALTH BROWSER SESSION...")
    session.playwright = await async_playwright().start()
    
    # Launch with Stealth Args
    session.browser = await session.playwright.chromium.launch(
        headless=True, # Headless hi rakho
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled", # CRITICAL: Hide Automation
            "--disable-infobars",
            "--window-size=1280,800"
        ]
    )
    
    # Create Context with Real User Agent & Viewport
    session.context = await session.browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="America/New_York"
    )
    
    session.page = await session.context.new_page()

    # --- STEALTH INJECTION: Hide WebDriver Property ---
    await session.page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)

    # Console Logs Capture
    session.page.on("console", lambda msg: print(f"[BROWSER-JS] {msg.text}", flush=True))
    
    log("   👉 Loading Website...")
    try:
        await session.page.goto("https://chatgptfree.ai/chat/", timeout=120000, wait_until="domcontentloaded")
    except Exception as e:
        log(f"   ⚠️ Goto Timeout: {e}")

    # Cloudflare Check
    selector = f"#aipkit_chat_container_{session.bot_id}"
    try:
        log(f"   ⏳ Waiting for Cloudflare check to pass...")
        # Thora lamba wait taake CF redirect kar de
        await session.page.wait_for_selector(selector, state="attached", timeout=60000)
        log("   ✅ Website Loaded & Cloudflare Passed!")
        
        # Extra wait for cookies to settle
        log("   ⏳ Sleeping 5s for cookies...")
        await asyncio.sleep(5)
        
    except Exception as e:
        log(f"   ❌ Selector Timeout. Dumping HTML:")
        content = await session.page.content()
        log(content[:1000])
    
    yield
    
    log("🔴 SHUTTING DOWN BROWSER...")
    if session.browser:
        await session.browser.close()
    if session.playwright:
        await session.playwright.stop()

app = FastAPI(lifespan=lifespan)

# --- Browser-Native Fetch Logic ---
async def execute_browser_request(message):
    
    # 1. Get Nonce
    log("   👉 Extracting Nonce...")
    try:
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
            log("   ❌ Nonce is NULL. Reloading page...")
            await session.page.reload()
            await session.page.wait_for_selector(f"#aipkit_chat_container_{session.bot_id}", state="attached", timeout=30000)
            nonce = await session.page.evaluate(nonce_script)
            
        log(f"   🔑 Nonce: {nonce}")
    except Exception as e:
        raise Exception(f"Nonce Extraction Failed: {e}")

    client_msg_id = f"aipkit-client-msg-{session.bot_id}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"

    # 2. JS Injection (Fetch)
    log("🚀 EXECUTING JS FETCH...")
    
    js_logic = """
    async ({ message, nonce, bot_id, client_id }) => {
        console.log("--- JS STARTING ---");
        
        const formData = new FormData();
        formData.append('action', 'aipkit_cache_sse_message');
        formData.append('message', message);
        formData.append('_ajax_nonce', nonce);
        formData.append('bot_id', bot_id);
        formData.append('user_client_message_id', client_id);

        console.log("Sending POST...");
        
        let postResp;
        try {
            postResp = await fetch('https://chatgptfree.ai/wp-admin/admin-ajax.php', {
                method: 'POST',
                body: formData,
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
        } catch (err) {
            console.error("Fetch Network Error:", err);
            return { error: "Network Error", details: err.toString() };
        }

        console.log("POST Status:", postResp.status);
        const rawText = await postResp.text();
        
        if (postResp.status !== 200) {
             console.log("Error Body:", rawText.substring(0, 500));
             return { error: "HTTP Error", status: postResp.status, body: rawText.substring(0, 500) };
        }

        let postJson;
        try {
            postJson = JSON.parse(rawText);
        } catch (e) {
            console.error("JSON Parse Failed");
            return { error: "JSON Parse Failed", raw_response: rawText.substring(0, 500) };
        }
        
        if (!postJson.success) {
            console.error("API Success False");
            return { error: "API Success False", details: postJson };
        }
        
        const cacheKey = postJson.data.cache_key;
        console.log("Got Cache Key:", cacheKey);

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

        console.log("Starting Stream...");
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
        console.log("Stream Complete. Length:", finalReply.length);
        return { success: true, reply: finalReply };
    }
    """

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
    
    if not session.page or session.page.is_closed():
        return {"error": "Browser crashed."}

    try:
        reply = await execute_browser_request(message)
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        log(f"❌ ERROR: {e}")
        # Agar 403 ya koi aur masla aya to Reload karo
        try:
            log("🔄 Reloading Page to bypass Cloudflare...")
            await session.page.reload()
            await session.page.wait_for_selector(f"#aipkit_chat_container_{session.bot_id}", state="attached", timeout=60000)
            await asyncio.sleep(5)
            # Retry request
            reply = await execute_browser_request(message)
            return {"response": reply, "status": "success"}
        except Exception as retry_e:
            return {"error": str(retry_e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
