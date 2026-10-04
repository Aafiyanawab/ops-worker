"""Two interchangeable browser backends behind one interface.
 - HttpBrowser: requests + BeautifulSoup (fast, deterministic, no install; used for tests/CI)
 - PlaywrightBrowser: real Chromium (use for the demo video)
Both expose the same *snapshot*: page text + numbered interactive elements. The model acts by ref."""
import json, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup


class TransientError(Exception):
    """5xx / network error: safe to retry a read."""


def clean_text(t):
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t)).strip()


def render_snapshot(url, status, title, text, elements, max_text=2500):
    out = [f"URL: {url}", f"HTTP status: {status}", f"Title: {title}", "--- page text ---",
           text[:max_text] + ("…[truncated]" if len(text) > max_text else ""),
           "--- interactive elements (refs only valid for THIS page state) ---"]
    for e in elements:
        s = f'[{e["ref"]}] {e["kind"]} "{e["label"]}"'
        if e.get("href"):
            s += f' -> {e["href"]}'
        if e.get("options"):
            s += " options=" + json.dumps(e["options"])
        if e.get("value"):
            s += f' value="{e["value"]}"'
        out.append(s)
    return "\n".join(out)


class HttpBrowser:
    def __init__(self, base_url, evidence_dir):
        self.base, self.dir = base_url.rstrip("/"), Path(evidence_dir)
        self.s, self.url, self.soup, self.status, self.elements, self.n = requests.Session(), None, None, None, [], 0

    # -- navigation
    def goto(self, url):
        try:
            r = self.s.get(urljoin((self.url or self.base) + "/", url), timeout=10)
        except requests.RequestException as e:
            raise TransientError(str(e))
        return self._load(r, raise_5xx=True)

    def _load(self, r, raise_5xx=False):
        self.status, self.url = r.status_code, r.url
        self.soup = BeautifulSoup(r.text, "html.parser")
        self.n += 1
        (self.dir / f"{self.n:03d}.html").write_text(r.text, encoding="utf-8")
        self._index()
        if raise_5xx and r.status_code >= 500:
            raise TransientError(f"HTTP {r.status_code} from {r.url}")
        return self.snapshot()

    def snapshot(self):
        title = self.soup.title.get_text(strip=True) if self.soup.title else ""
        return render_snapshot(self.url, self.status, title, clean_text(self.soup.get_text("\n")), self.elements)

    def evidence(self):
        return str(self.dir / f"{self.n:03d}.html")

    # -- indexing
    def _label(self, el):
        if el.get("placeholder") or el.get("aria-label"):
            return el.get("placeholder") or el.get("aria-label")
        lab = el.get("id") and self.soup.find("label", attrs={"for": el["id"]})
        return lab.get_text(strip=True) if lab else (el.get("name") or "field")

    def _index(self):
        self.elements = []
        for el in self.soup.find_all(["a", "input", "select", "textarea", "button"]):
            typ = (el.get("type") or "text").lower()
            if (el.name == "input" and typ == "hidden") or (el.name == "a" and not el.get("href")):
                continue
            e = {"ref": len(self.elements) + 1, "el": el, "name": el.get("name")}
            if el.name == "a":
                e.update(kind="link", label=el.get_text(" ", strip=True), href=el["href"])
            elif el.name == "button" or (el.name == "input" and typ in ("submit", "button")):
                e.update(kind="button", label=el.get_text(" ", strip=True) or el.get("value") or "button")
            elif el.name == "select":
                opts = [o.get("value", o.get_text(strip=True)) for o in el.find_all("option")]
                e.update(kind="select", label=self._label(el), options=opts, value=opts[0] if opts else "")
            else:
                e.update(kind="password" if typ == "password" else "textbox", label=self._label(el),
                         value="" if typ == "password" else el.get("value", ""))
            self.elements.append(e)

    def _get(self, ref):
        if not 1 <= ref <= len(self.elements):
            raise ValueError(f"ref {ref} does not exist on this page (have 1..{len(self.elements)}). Take a fresh snapshot.")
        return self.elements[ref - 1]

    # -- actions
    def fill(self, ref, value):
        e = self._get(ref)
        if e["kind"] not in ("textbox", "password"):
            raise ValueError(f"ref {ref} is a {e['kind']}, not a text field")
        e["value"] = value
        return self.snapshot()

    def select(self, ref, value):
        e = self._get(ref)
        if e["kind"] != "select" or value not in e["options"]:
            raise ValueError(f"ref {ref} is not a select with option {value!r}")
        e["value"] = value
        return self.snapshot()

    def _form_data(self, e):
        form = e["el"].find_parent("form")
        data = {i["name"]: i.get("value", "") for i in form.find_all("input", attrs={"type": "hidden"}) if i.get("name")}
        for o in self.elements:
            if o["el"].find_parent("form") is form and o.get("name") and o["kind"] in ("textbox", "password", "select"):
                data[o["name"]] = o["value"]
        if e["el"].get("name"):
            data[e["el"]["name"]] = e["el"].get("value", "")
        return form, data

    def describe_click(self, ref):
        """Used by the harness to decide if a click is a state-changing write."""
        e = self._get(ref)
        if e["kind"] != "button" or not e["el"].find_parent("form"):
            return {"is_write": False, "description": f"click {e['kind']} '{e['label']}'"}
        form, data = self._form_data(e)
        method = (form.get("method") or "get").lower()
        shown = {k: ("***" if any(o.get("name") == k and o["kind"] == "password" for o in self.elements) else v) for k, v in data.items()}
        is_login = any(o["kind"] == "password" and o["el"].find_parent("form") is form for o in self.elements)
        return {"is_write": method == "post" and not is_login, "description": f"{method.upper()} {form.get('action') or self.url} with {shown} (button '{e['label']}')",
                "label": e["label"], "fields": shown}

    def click(self, ref):
        e = self._get(ref)
        if e["kind"] == "link":
            return self.goto(e["href"])
        if e["kind"] != "button":
            raise ValueError(f"ref {ref} is a {e['kind']}, not clickable")
        form, data = self._form_data(e)
        url = urljoin(self.url, form.get("action") or self.url)
        try:
            r = self.s.post(url, data=data, timeout=10) if (form.get("method") or "get").lower() == "post" else self.s.get(url, params=data, timeout=10)
        except requests.RequestException as ex:
            raise TransientError(str(ex))
        return self._load(r)

    def close(self):
        pass


class PlaywrightBrowser:
    INDEX_JS = """() => {
      const vis=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
      const els=[...document.querySelectorAll('a[href],input,select,textarea,button')]
        .filter(e=>!(e.tagName==='INPUT'&&e.type==='hidden')&&vis(e));
      return els.map((e,i)=>{e.setAttribute('data-ref',i+1);
        const t=e.tagName.toLowerCase(), type=(e.type||'').toLowerCase();
        const kind=t==='a'?'link':(t==='button'||(t==='input'&&['submit','button'].includes(type)))?'button':t==='select'?'select':type==='password'?'password':'textbox';
        const label=(e.getAttribute('aria-label')||e.placeholder||(e.labels&&e.labels[0]&&e.labels[0].innerText)||e.innerText||e.value||e.name||'').trim();
        return {ref:i+1,kind,label,href:e.getAttribute('href'),name:e.name||null,
          value:(kind==='password'||kind==='button'||kind==='link')?'':(e.value||''),
          options:t==='select'?[...e.options].map(o=>o.value):null,
          method:e.form?(e.form.method||'get').toLowerCase():null,
          login:e.form?!!e.form.querySelector('input[type=password]'):false};});}"""

    def __init__(self, base_url, evidence_dir, headless=True):
        from playwright.sync_api import sync_playwright
        self.base, self.dir, self.n, self.status = base_url.rstrip("/"), Path(evidence_dir), 0, None
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless)
        self.page = self.browser.new_page()
        self.page.on("response", self._on_resp)
        self.elements = []

    def _on_resp(self, r):
        if r.request.is_navigation_request() and r.frame == self.page.main_frame:
            self.status = r.status

    def _after(self, raise_5xx=False):
        self.page.wait_for_load_state()
        self.elements = self.page.evaluate(self.INDEX_JS)
        self.n += 1
        self.page.screenshot(path=str(self.dir / f"{self.n:03d}.png"))
        if raise_5xx and (self.status or 0) >= 500:
            raise TransientError(f"HTTP {self.status} from {self.page.url}")
        return self.snapshot()

    def goto(self, url):
        try:
            self.page.goto(urljoin(self.page.url if self.page.url.startswith("http") else self.base + "/", url))
        except Exception as e:
            raise TransientError(str(e))
        return self._after(raise_5xx=True)

    def snapshot(self):
        text = clean_text(self.page.inner_text("body"))
        return render_snapshot(self.page.url, self.status, self.page.title(), text, self.elements)

    def evidence(self):
        return str(self.dir / f"{self.n:03d}.png")

    def _get(self, ref):
        if not 1 <= ref <= len(self.elements):
            raise ValueError(f"ref {ref} does not exist on this page. Take a fresh snapshot.")
        return self.elements[ref - 1]

    def fill(self, ref, value):
        e = self._get(ref)
        if e["kind"] not in ("textbox", "password"):
            raise ValueError(f"ref {ref} is a {e['kind']}, not a text field")
        self.page.fill(f'[data-ref="{ref}"]', value)
        e["value"] = "" if e["kind"] == "password" else value
        return self.snapshot()

    def select(self, ref, value):
        self._get(ref)
        self.page.select_option(f'[data-ref="{ref}"]', value)
        return self.snapshot()

    def describe_click(self, ref):
        e = self._get(ref)
        if e["kind"] != "button" or not e["method"]:
            return {"is_write": False, "description": f"click {e['kind']} '{e['label']}'"}
        fields = self.page.evaluate("""r=>{const f=document.querySelector(`[data-ref="${r}"]`).form;
            return Object.fromEntries([...new FormData(f)].filter(([k])=>{const i=f.elements[k];return !(i&&i.type==='password')}))}""", ref)
        return {"is_write": e["method"] == "post" and not e["login"], "description": f"{e['method'].upper()} form with {fields} (button '{e['label']}')",
                "label": e["label"], "fields": fields}

    def click(self, ref):
        self._get(ref)
        self.page.click(f'[data-ref="{ref}"]')
        return self._after()

    def close(self):
        self.browser.close()
        self.pw.stop()
