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
    """Python Logs"""
    print(f"[LOG] {text}", flush=True)

# --- Startup & Shutdown Logic ---
@asynccontextmanager
async def lifespan(app: FastAPI):
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

    # --- BROWSER CONSOLE LOGS KO PYTHON ME PRINT KARNA ---
    # Ye bohot zaroori hai debugging ke liye
    session.page.on("console", lambda msg: print(f"[BROWSER-JS] {msg.text}", flush=True))
    
    log("   👉 Loading https://chatgptfree.ai/chat/ ...")
    try:
        await session.page.goto("https://chatgptfree.ai/chat/", timeout=120000, wait_until="domcontentloaded")
    except Exception as e:
        log(f"   ⚠️ Goto Timeout (might be ok): {e}")

    # Cloudflare Check
    selector = f"#aipkit_chat_container_{session.bot_id}"
    try:
        log(f"   ⏳ Waiting for selector: {selector}...")
        await session.page.wait_for_selector(selector, state="attached", timeout=60000)
        log("   ✅ Website Loaded & Cloudflare Passed!")
    except Exception as e:
        log(f"   ❌ Timeout waiting for selector. Dumping HTML Snippet:")
        content = await session.page.content()
        log(content[:1000]) # First 1000 chars print karo taake pata chale kya khula hai
    
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
    log("   👉 Extracting Nonce from Page...")
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
            log("   ❌ Nonce is NULL. Dumping Page Content for debugging...")
            content = await session.page.content()
            log(content[:500])
            raise Exception("Nonce not found on page")
        log(f"   🔑 Nonce: {nonce}")
    except Exception as e:
        raise Exception(f"Nonce Extraction Failed: {e}")

    client_msg_id = f"aipkit-client-msg-{session.bot_id}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"

    # 2. JS Injection (With HARD LOGGING)
    log("🚀 EXECUTING JS FETCH INSIDE BROWSER...")
    
    js_logic = """
    async ({ message, nonce, bot_id, client_id }) => {
        console.log("--- JS STARTING ---");
        
        // --- STEP 1: POST ---
        const formData = new FormData();
        formData.append('action', 'aipkit_cache_sse_message');
        formData.append('message', message);
        formData.append('_ajax_nonce', nonce);
        formData.append('bot_id', bot_id);
        formData.append('user_client_message_id', client_id);

        console.log("Sending POST to admin-ajax.php...");
        
        let postResp;
        try {
            postResp = await fetch('https://chatgptfree.ai/wp-admin/admin-ajax.php', {
                method: 'POST',
                body: formData,
                headers: {
                    'X-Requested-With': 'XMLHttpRequest' // Ye header zaroori hota hai WordPress ajax ke liye
                }
            });
        } catch (err) {
            console.error("Fetch Network Error:", err);
            return { error: "Network Error", details: err.toString() };
        }

        console.log("POST Status:", postResp.status);
        
        // RAW TEXT READ KARO (Crash se bachne ke liye)
        const rawText = await postResp.text();
        console.log("Raw Response Length:", rawText.length);
        
        // Agar response chhota hai to print kar do, bara hai to shuru ka hissa
        if (rawText.length < 500) {
            console.log("Raw Body:", rawText);
        } else {
            console.log("Raw Body Start:", rawText.substring(0, 500));
        }

        let postJson;
        try {
            postJson = JSON.parse(rawText);
        } catch (e) {
            console.error("JSON Parse Failed! Server returned HTML/String instead of JSON.");
            return { error: "JSON Parse Failed", raw_response: rawText };
        }
        
        if (!postJson.success) {
            console.error("API returned success: false");
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

        console.log("Starting EventStream...");
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
        console.log("Stream Complete. Final Length:", finalReply.length);
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
        # Error return karo taake log me nazar aye ke masla kya tha (HTML ya JSON)
        raise Exception(f"JS Error: {result}")
    
    return result["reply"]

@app.get("/chat")
async def chat_endpoint(message: str = Query(..., description="User message")):
    log(f"📨 REQUEST: {message}")
    
    if not session.page or session.page.is_closed():
        return {"error": "Browser crashed or closed."}

    try:
        reply = await execute_browser_request(message)
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        log(f"❌ Python Error Catch: {e}")
        
        # Agar error aya hai, to browser shayad stuck ho, reload kar lo
        try:
            log("🔄 Reloading Page to refresh tokens...")
            await session.page.reload()
            # Reload ke baad dubara wait karo element ka
            await session.page.wait_for_selector(f"#aipkit_chat_container_{session.bot_id}", state="attached", timeout=30000)
        except Exception as reload_e:
            log(f"   ❌ Reload failed: {reload_e}")

        return {"error": str(e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
