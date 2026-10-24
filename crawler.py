import sqlite3, time

from urllib.parse import urlparse, urldefrag
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By

MAX_PAGES = 5
DELAY = 2
WEB_PAGE = "https://books.toscrape.co"
DB_NAME = "scrape.db"

def init_db(database):
    db = sqlite3.connect(database)
    db.execute("""CREATE TABLE IF NOT EXISTS pages(
            url TEXT PRIMARY KEY, domain TEXT, title TEXT,
            text TEXT, html TEXT, body TEXT, depth INT,
            crawled_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return db

def normalize(url):
    url, _ = urldefrag(url)
    return url.rstrip("/")

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    # opts.add_argument("--no-sandbox")
    # opts.add_argument("--disable-dev-shm-usage")
    # opts.add_argument("--disable-blink-features=AutomationControlled")
    # opts.add_argument("--user-agent")
    return webdriver.Chrome(options=opts)

def crawl(start_url, max_pages, delay):
    domain = urlparse(start_url).netloc
    driver, db = make_driver(), init_db(DB_NAME)
    start = normalize(start_url)
    queue, seen, count = [(start, 0)], set(), 0

    try:
        while queue and count < max_pages:
            url, depth = queue.pop()
            try:
                print(f"Requesting {url} depth={depth}")
                driver.get(url)
                time.sleep(delay)
                text = driver.find_element(By.TAG_NAME, "body").text
                db.execute(
                    "INSERT OR REPLACE INTO pages (url, domain, title, text, html, depth) "
                    "VALUES (?,?,?,?,?,?)",
                    (url, domain, driver.title, text, driver.page_source, depth))
                db.commit()
                count += 1
                print(f"[{count}/{max_pages}] depth={depth} {url}")

                for link in driver.find_elements(By.TAG_NAME, "a"):
                    href = link.get_attribute("href")
                    if not href or not href.startswith(("http://", "https://")):
                        continue
                    link = normalize(href or "")
                    if urlparse(link).netloc == domain and link not in seen:
                        seen.add(link)
                        queue.append((link, depth + 1))
            except Exception as e:
                print(f"Error crawling {url}: {e}")
                continue

    finally:
        db.close()
        driver.quit()

if __name__ == "__main__":
    crawl(WEB_PAGE, MAX_PAGES, DELAY)
    print("Crawling complete")