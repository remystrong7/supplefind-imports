import csv, datetime, re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from slugify import slugify
import yaml

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
}

def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def text_or_empty(node):
    return re.sub(r"\s+", " ", node.get_text(strip=True)) if node else ""

def attr_any(el, names):
    """Return the first present attribute value from a list of names."""
    for n in names:
        if el and el.has_attr(n) and el.get(n):
            return el.get(n)
    return ""

def find_next_page(soup, base):
    # Try common "next" patterns
    next_link = soup.select_one('a[rel="next"], a.next, a.pagination__next, .pagination a[aria-label*="Next" i]')
    if not next_link:
        # fallback: link text
        for a in soup.select("a"):
            if text_or_empty(a).lower() in ("next", "›", "»"):
                next_link = a
                break
    return urljoin(base, next_link.get("href")) if next_link and next_link.get("href") else None

def scrape_vendor(vendor):
    results = []
    base = vendor["domain"].rstrip("/") + "/"
    url  = vendor["products_url"]
    sel  = vendor["selectors"]

    seen_links = set()
    page_count = 0

    while url and page_count < 20:  # hard safety limit
        page_count += 1
        soup = get_soup(url)

        for item in soup.select(sel["product"]):
            # Name
            name = text_or_empty(item.select_one(sel["name"]))
            if not name:
                continue

            # Link
            a = item.select_one(sel["link"])
            href = a.get("href") if a else ""
            link = urljoin(base, href)
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            # Image
            img = item.select_one(sel["image"])
            img_url = attr_any(img, ["src", "data-src", "data-original", "data-image", "data-srcset"])
            if img_url and " " in img_url and "http" in img_url:
                # if srcset, take the first URL
                img_url = img_url.split(" ")[0]
            img_url = urljoin(base, img_url) if img_url else ""

            # Price
            price = text_or_empty(item.select_one(sel.get("price","")))

            # Availability (optional selector)
            availability = ""
            if "availability" in sel:
                availability = text_or_empty(item.select_one(sel["availability"]))
            if not availability:
                # heuristic: sold-out badges or classes
                classes = " ".join(item.get("class", []))
                if "sold" in classes.lower():
                    availability = "Sold out"

            results.append({
                "vendor": vendor["name"],
                "sku": "",  # can be filled later from product page if needed
                "name": name,
                "price": price,
                "availability": availability,
                "product_url": link,
                "image_url": img_url,
                "slug": slugify(name),
            })

        # pagination
        url = find_next_page(soup, base)

    return results

def main():
    with open("vendors.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    all_rows = []
    for v in config.get("vendors", []):
        try:
            print(f"Scraping {v['name']}…")
            all_rows.extend(scrape_vendor(v))
        except Exception as e:
            print(f"ERROR with {v.get('name')}: {e}")

    # Write a single CSV for WP All Import
    headers = ["vendor", "sku", "name", "price", "availability", "product_url", "image_url", "slug"]
    with open("products.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for row in all_rows:
            w.writerow(row)
    print(f"Wrote {len(all_rows)} rows to products.csv")

if __name__ == "__main__":
    main()
