import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright

async def run():
    dump_mode = "--dump" in sys.argv
    os.makedirs("mozzart_dump", exist_ok=True)
    captured_json = []

    async with async_playwright() as p:
        print("Pokrećem pretraživač...")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        async def handle_response(response):
            url = response.url
            # Hvatamo SAMO one zahteve koji u sebi imaju "event" (isključujemo struct/meni)
            if "application/json" in response.headers.get("content-type", "") and "event" in url:
                try:
                    data = await response.json()
                    captured_json.append({"url": url, "data": data})
                    print(f"[UVAĆEN DOGAĐAJ] {url[:90]}")
                except Exception:
                    pass

        page.on("response", handle_response)

        print("Otvaram Superbet košarku...")
        await page.goto("https://superbet.rs/sportske-opklade/kosarka", wait_until="domcontentloaded")
        
        print("\n>>> SADA IMAŠ 30 SEKUNDI DA KLIKNEŠ NA NEKI MEČ EVROLIGE U BROWSERU! <<<\n")
        await asyncio.sleep(30)

        print("Zatvaram pretraživač i čuvam podatke...")
        await browser.close()

    if dump_mode:
        with open("mozzart_dump/raw.json", "w", encoding="utf-8") as f:
            json.dump(captured_json, f, ensure_ascii=False, indent=2)
        print(f"\nUspešno sačuvano {len(captured_json)} event odgovora u mozzart_dump/raw.json!")

if __name__ == "__main__":
    asyncio.run(run())