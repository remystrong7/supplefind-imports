import os, csv, sys, time, re, json
from urllib.parse import urljoin, urlparse, urlencode
import requests
from bs4 import BeautifulSoup

USER_AGENT = os.getenv("USER_AGENT","Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36")
HEADERS_BASE = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

import yaml
with open("vendors.yaml","r",encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

def get_session(cookie_value=None):
    s = requests.Session()
    s.headers.update(HEADERS_BASE)
    if cookie_value:
        s.headers["Cookie"] = cookie_value
    return s

def clean_text(x):
    if not x: return ""
    return re.sub(r"\s+", " ", x).strip()

def first(soup, selectors):
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(strip=True)
            if txt: return txt
    return ""

def first_attr(soup, selectors, attr):
    for sel in selectors:
        el = soup.select_one(sel)
        if el and el.get(attr):
            return el.get(attr).strip()
    return ""

def guess_price(soup):
    # try meta first
    m = first_attr(soup, ['meta[property="product:price:amount"]','meta[itemprop="price"]'], "content")
    if m: return m
    # common selectors
    t = first(soup, ['[itemprop=price]','span.price','span.woocommerce-Price-amount','div.price','p.price'])
    t = re.sub(r"[^\d\.,]", "", t)
    t = t.replace(",", "")  # simple normalize
    return t or ""

def guess_currency(soup):
    c = first_attr(soup, ['meta[property="product:price:currency"]','meta[itemprop="priceCurrency"]'], "content")
    if c: return c
    return "USD"

def guess_sku(soup):
    sku = first(soup, ['[itemprop=sku]','span.sku','div.sku','p.sku','span.product-sku','div.product-sku'])
    if not sku:
        # look for "SKU: 123"
        text = soup.get_text(" ", strip=True)
        m = re.search(r"\bSKU[:\s#]*([A-Za-z0-9\-\._/]+)\b", text, re.I)
        if m: sku = m.group(1)
    return clean_text(sku)

def guess_stock(soup):
    txt = soup.get_text(" ", strip=True).lower()
    if "out of stock" in txt or "sold out" in txt:
        return "outofstock"
    # Woo/Shopify buttons
    btn = soup.select_one("button.add_to_cart_button, button[name='add'], button#AddToCart, form[action*='cart'] button")
    return "instock" if btn else "instock"

def guess_image(soup):
    og = first_attr(soup, ['meta[property="og:image"]','meta[name="og:image"]'], "content")
    if og: return og
    return first_attr(soup, ["img#landingImage","img.wp-post-image","img.attachment-shop_single","img.product-single__photo","img[itemprop=image]","img"], "src") or ""

def guess_description(soup):
    desc = first(soup, ["div#tab-description","div.product-short-description","div.product_description","div[itemprop=description]","div#description","section.product-description","div.product-description","div.woocommerce-Tabs-panel--description","div#tab-1"])
    if not desc:
        # fallback to the longest paragraph block
        ps = sorted((p.get_text(" ", strip=True) for p in soup.select("p")), key=lambda x: len(x), reverse=True)
        desc = ps[0] if ps else ""
    return clean_text(desc)

def find_links(soup, base_url):
    links = set()
    for a in soup.select("a[href]"):
        href = a["href"]
        if not href: continue
        if any(x in href.lower() for x in ["/product", "/products", "/item", "/shop/", "/p/", "/detail"]):
            links.add(urljoin(base_url, href))
    # also keep collection cards with data-product-handle
    for el in soup.select("[data-product-handle]"):
        handle = el.get("data-product-handle")
        if handle:
            links.add(urljoin(base_url, f"/products/{handle}"))
    return list(links)

def list_pages(base_url, list_url):
    # very simple paginator for Shopify-like ?page=
    urls = [list_url]
    parsed = urlparse(list_url)
    sep = "&" if parsed.query else "?"
    for p in range(2, 51):
        urls.append(f"{list_url}{sep}page={p}")
    return urls

def scrape_vendor(v):
    name = v["name"]
    base = v["base_url"]
    brand_override = v.get("brand_override")
    auth = v.get("auth", {"method":"none"})
    cookie = None
    if auth.get("method") == "cookie":
        cookie_env = auth.get("cookie_env")
        cookie = os.getenv(cookie_env, "")
    s = get_session(cookie)

    seen = set()
    products = []
    for start_url in v["list_urls"]:
        for page_url in list_pages(base, start_url):
            try:
                r = s.get(page_url, timeout=30)
                if r.status_code >= 400: break
                soup = BeautifulSoup(r.text, "lxml")
            except Exception:
                break

            links = find_links(soup, base)
            if not links:
                # stop paging if nothing new
                break

            for url in links:
                if url in seen: continue
                seen.add(url)
                try:
                    pr = s.get(url, timeout=30)
                    if pr.status_code >= 400: continue
                    ps = BeautifulSoup(pr.text, "lxml")
                    title = clean_text(first(ps, ["h1.product_title","h1.product-title","h1.product-name","h1","meta[property='og:title']"]))
                    price = guess_price(ps)
                    currency = guess_currency(ps)
                    sku = guess_sku(ps)
                    stock = guess_stock(ps)
                    img = guess_image(ps)
                    desc = guess_description(ps)

                    # ingredients (best-effort)
                    ing = ""
                    for lbl in ["ingredients", "supplement facts", "facts"]:
                        m = re.search(rf"{lbl}\s*[:\-–]\s*(.+?)\s{1,10}[A-Z][a-z]{{2,}}", ps.get_text(" ", strip=True), re.I)
                        if m:
                            ing = m.group(1)[:500]
                            break

                    # brand
                    brand = brand_override or clean_text(first(ps, ["span.brand","div.brand a","a.brand"]))
                    # dedupe key
                    key = sku or url
                    products.append({
                        "vendor": name,
                        "brand": brand or "",
                        "sku": sku or "",
                        "name": title or "",
                        "price": price or "",
                        "currency": currency or "USD",
                        "stock_status": stock,
                        "inventory": "1" if stock=="instock" else "0",
                        "product_url": url,
                        "image_url": img or "",
                        "ingredients": ing,
                        "description": desc,
                        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception:
                    continue
    return products

def dedupe(rows):
    out = {}
    for r in rows:
        key = (r.get("sku") or "").strip().lower() or r.get("product_url").lower()
        # prefer rows that have price/sku/img
        cur = out.get(key)
        score = (1 if r.get("price") else 0) + (1 if r.get("sku") else 0) + (1 if r.get("image_url") else 0)
        if not cur:
            out[key]= (score, r)
        else:
            if score > cur[0]:
                out[key]= (score, r)
    return [r for _,r in out.values()]

def main():
    all_rows=[]
    for v in CFG["vendors"]:
        rows = scrape_vendor(v)
        print(f"::notice:: {v['name']} scraped {len(rows)} rows")
        all_rows.extend(rows)
    rows = dedupe(all_rows)
    print(f"::notice:: After dedupe: {len(rows)} rows")

    fields = ["vendor","brand","sku","name","price","currency","stock_status","inventory","product_url","image_url","ingredients","description","scraped_at"]
    with open("products.csv","w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("::notice:: Wrote products.csv")

if __name__ == "__main__":
    sys.exit(main())
