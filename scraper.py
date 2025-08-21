import csv, sys, time, os
from datetime import datetime
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from slugify import slugify
import yaml

USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

OUTFILE = "products.csv"
FIELDS = [
    "vendor","sku","name","price","currency",
    "stock_status","product_url","image_url",
    "ingredients","description","scraped_at"
]

def read_yaml(path="vendors.yaml"):
    if not os.path.exists(path):
        print("WARNING: vendors.yaml not found. Creating an empty CSV.")
        write_csv([])
        sys.exit(0)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def safe_get(url, timeout=20):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code >= 400:
            print(f"HTTP {r.status_code} for {url}")
            return None
        return r
    except Exception as e:
        print(f"Request error for {url}: {e}")
        return None

def pick_text(soup, selectors):
    """Return first non-empty text from list of CSS selectors (BeautifulSoup)."""
    if not selectors: return ""
    for sel in selectors:
        # handle ::attr(...) shorthand
        attr = None
        if "::attr(" in sel:
            sel, attr = sel.split("::attr(")[0].strip(), sel.split("::attr(")[1].split(")")[0]
        node = soup.select_one(sel)
        if not node: 
            # light support for meta[property="..."] in selector with attr content
            if sel.startswith('meta[') and not attr:
                meta = soup.select_one(sel)
                if meta and meta.get("content"): 
                    return meta.get("content").strip()
            continue
        if attr:
            val = node.get(attr)
            if val: return val.strip()
        else:
            txt = node.get_text(" ", strip=True)
            if txt: return txt
    return ""

def product_links(base_url, list_cfg):
    seen, links = set(), []
    for page in list_cfg:
        url = page.get("url")
        sel = page.get("item_selector")
        if not url or not sel: 
            continue
        resp = safe_get(url)
        if not resp: 
            continue
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.select(sel):
            href = a.get("href")
            if not href: 
                continue
            url_abs = urljoin(base_url, href)
            # keep only product-like URLs
            if "/products/" not in url_abs:
                continue
            if url_abs not in seen:
                seen.add(url_abs)
                links.append(url_abs)
    print(f"Found {len(links)} product links")
    return links

def scrape_product(url, vendor, page_cfg, currency=""):
    resp = safe_get(url)
    if not resp:
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    name = pick_text(soup, page_cfg.get("title", []))
    price_raw = pick_text(soup, page_cfg.get("price", []))
    # normalize price to just digits and dot if present
    price = ""
    if price_raw:
        import re
        m = re.search(r"[\d,.]+", price_raw)
        if m:
            price = m.group(0).replace(",", "")
    image = pick_text(soup, page_cfg.get("image", []))
    if image and image.startswith("//"):
        image = "https:" + image
    elif image and image.startswith("/"):
        image = urljoin(url, image)
    desc = pick_text(soup, page_cfg.get("description", []))
    sku = slugify(name)[:60] if name else slugify(urlparse(url).path.split("/")[-1])[:60]
    row = {
        "vendor": vendor,
        "sku": sku or "",
        "name": name or "",
        "price": price or "",
        "currency": currency or "",
        "stock_status": "",   # can add if you locate an availability element
        "product_url": url,
        "image_url": image or "",
        "ingredients": "",    # can be parsed from desc later if you want
        "description": desc or "",
        "scraped_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"
    }
    return row

def write_csv(rows):
    with open(OUTFILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"Wrote {len(rows)} rows to {OUTFILE}")

def main():
    cfg = read_yaml()
    vendors = cfg.get("vendors", [])
    all_rows = []
    for v in vendors:
        name = v.get("name","")
        base = v.get("base_url","")
        if not base:
            print(f"Skip vendor {name}: missing base_url")
            continue
        print(f"=== {name} ===")
        links = product_links(base, v.get("product_list", []))
        for i, url in enumerate(links, start=1):
            print(f"[{i}/{len(links)}] {url}")
            row = scrape_product(url, name, v.get("product_page", {}), v.get("currency",""))
            if row:
                all_rows.append(row)
            time.sleep(0.5)  # be polite
    write_csv(all_rows)

if __name__ == "__main__":
    main()
