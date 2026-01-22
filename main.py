import uvicorn
from fastapi import FastAPI, Query
from playwright.async_api import async_playwright
from contextlib import asynccontextmanager
import asyncio
import os

app = FastAPI()

# --- Global Session Manager ---
class BrowserSession:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.is_ready = False

session = BrowserSession()

def log(text):
    print(f"[LOG] {text}", flush=True)

async def close_browser():
    """Browser ko mukammal band karta hai"""
    log("🔴 KILLING BROWSER SESSION...")
    if session.page: await session.page.close()
    if session.context: await session.context.close()
    if session.browser: await session.browser.close()
    if session.playwright: await session.playwright.stop()
    session.is_ready = False

async def init_browser():
    """Naya browser session shuru karta hai"""
    if session.is_ready:
        return

    log("🔵 INITIALIZING NEW FRESH BROWSER...")
    try:
        session.playwright = await async_playwright().start()
        
        # Heavy Chrome Launch (Stealth Arguments)
        session.browser = await session.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars"
            ]
        )
        
        # Fresh Context (Har baar nayi identity)
        session.context = await session.browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36"
        )
        
        session.page = await session.context.new_page()

        # Stealth JS Injection
        await session.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        # Browser Console Logs (Debugging ke liye)
        session.page.on("console", lambda msg: print(f"[BROWSER] {msg.text}"))

        log("   👉 Loading DeepAI Homepage (to load scripts)...")
        # Hum main page load karenge taake 'generateTryitApiKey' function memory me aa jaye
        await session.page.goto("https://deepai.org/chat", timeout=60000, wait_until="domcontentloaded")
        
        log("   ✅ DeepAI Loaded! Scripts are ready.")
        session.is_ready = True

    except Exception as e:
        log(f"   ❌ Init Failed: {e}")
        await close_browser()

# --- Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_browser()
    yield
    await close_browser()

app = FastAPI(lifespan=lifespan)

# --- The Heavy Logic ---
async def send_deepai_message(message):
    if not session.is_ready:
        await init_browser()

    log(f"🚀 Executing JS Fetch for: {message}")

    # Ye JS script browser ke andar chalega.
    # Ye wahi 'generateTryitApiKey()' use karega jo unki website par hai.
    js_script = """
    async (userMessage) => {
        try {
            // 1. Generate Dynamic API Key using their internal function
            const apiKey = generateTryitApiKey(); 
            console.log("Generated Key:", apiKey);

            // 2. Prepare Form Data
            const formData = new FormData();
            formData.append('chat_style', 'chat');
            formData.append('chatHistory', JSON.stringify([{ role: "user", content: userMessage }]));
            formData.append('model', 'gemini-2.5-flash-lite'); // Model change kar sakte ho
            formData.append('hacker_is_stinky', 'very_stinky'); // Anti-bot bypass param
            formData.append('enabled_tools', JSON.stringify(["image_generator","image_editor"]));

            // 3. Fetch Request (Directly from browser to api.deepai.org)
            const response = await fetch('https://api.deepai.org/hacking_is_a_serious_crime', {
                method: 'POST',
                headers: {
                    'api-key': apiKey
                },
                body: formData
            });

            if (response.status !== 200) {
                return { error: true, status: response.status, body: await response.text() };
            }

            // 4. Read Stream Response
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullText = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                fullText += decoder.decode(value, { stream: true });
            }

            return { success: true, text: fullText };

        } catch (err) {
            return { error: true, message: err.toString() };
        }
    }
    """

    try:
        # Browser ke andar JS run karo
        result = await session.page.evaluate(js_script, message)
        
        # Check Results
        if result.get("error"):
            log(f"   ❌ Browser JS Error: {result}")
            # Agar 401/403 aye ya "limit" ka error aye, to session kill karo
            if result.get("status") in [401, 403] or "login" in str(result.get("body", "")).lower():
                raise Exception("Session Expired / Limit Reached")
            return f"Error: {result}"
        
        log(f"   🎉 Success! Response Length: {len(result['text'])}")
        return result['text']

    except Exception as e:
        log(f"⚠️ Exception: {e}")
        raise e

@app.get("/chat")
async def chat_endpoint(message: str = Query(..., description="User message")):
    log(f"📨 REQUEST: {message}")
    
    try:
        reply = await send_deepai_message(message)
        return {"response": reply, "status": "success"}
    
    except Exception as e:
        log("🔄 Session seems dead. RESTARTING BROWSER & RETRYING...")
        
        # Hard Reset: Browser band karo aur naya kholo
        await close_browser()
        await init_browser()
        
        try:
            # Dobara try karo
            reply = await send_deepai_message(message)
            return {"response": reply, "status": "success"}
        except Exception as final_e:
            return {"error": str(final_e), "status": "failed"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
