"""업비트 지원 네트워크·공지 수집.

업비트 웹사이트가 쓰는 공개 엔드포인트(로그인 불필요)를 읽습니다.
- 출금 수수료 안내: 코인별 지원 네트워크(net_type, network_name)와 수수료
- 입출금 현황: 코인별 입출금 상태
- 마켓 목록(공식 API): 한글 이름, 거래 마켓, 유의 종목
- 공지사항
공식 Open API가 아닌 주소가 섞여 있어 업비트가 바꾸면 수정이 필요할 수 있습니다.
"""

import html
import json
import re
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CoinNewsBrief/1.0"
FEE_URL = "https://ccx.upbit.com/api/v1/status/withdraw_fee"
WALLET_URL = "https://ccx.upbit.com/api/v1/status/wallet"
MARKET_URL = "https://api.upbit.com/v1/market/all?is_details=true"
NOTICE_URL = "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=30&category=all"
NOTICE_LINK = "https://upbit.com/service_center/notice?id={id}"

WALLET_STATE = {
    "working": "정상", "paused": "입출금 중단", "deposit_only": "입금만 가능",
    "withdraw_only": "출금만 가능", "unsupported": "미지원",
}

# 알림 대상 공지: 이 카테고리이거나, 제목에 키워드가 있는 공지
NOTICE_CATEGORIES = {"입출금", "거래"}
NOTICE_KEYWORDS = re.compile(
    r"네트워크|업그레이드|하드포크|메인넷|스왑|입출금|거래지원 종료|유의 종목|보안|해킹|출금 주의")


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"{url.split('?')[0]} → {e}") from e


def fetch_networks():
    """코인·네트워크 목록. [{currency, korean_name, net_type, network_name, markets, state, ...}]"""
    fees = get_json(FEE_URL)["withdraw_fee_conditions"]
    wallet = {w["currency"]: w for w in get_json(WALLET_URL)}
    markets = {}
    for m in get_json(MARKET_URL):
        quote, base = m["market"].split("-", 1)
        info = markets.setdefault(base, {"korean_name": m["korean_name"], "english_name": m["english_name"],
                                         "markets": [], "warning": False})
        info["markets"].append(quote)
        info["warning"] |= bool((m.get("market_event") or {}).get("warning"))

    rows = []
    for f in fees:
        if not f.get("net_type"):  # KRW
            continue
        cur = f["currency"]
        mk = markets.get(cur, {})
        w = wallet.get(cur, {})
        rows.append({
            "currency": cur,
            "korean_name": mk.get("korean_name", ""),
            "english_name": mk.get("english_name", ""),
            "markets": sorted(mk.get("markets", []), key=lambda q: {"KRW": 0, "BTC": 1, "USDT": 2}.get(q, 9)),
            "warning": mk.get("warning", False),
            "net_type": f["net_type"],
            "network_name": f.get("network_name") or f["net_type"],
            "withdraw_fee": f.get("withdraw_fee"),
            "state": w.get("wallet_state", ""),
            "block_state": w.get("block_state", ""),
        })
    return rows


def fetch_notices():
    data = get_json(NOTICE_URL)["data"]
    return data.get("notices") or data.get("list") or []


def is_relevant_notice(n):
    return n.get("category") in NOTICE_CATEGORIES or bool(NOTICE_KEYWORDS.search(n.get("title", "")))


def affected_coins(title, rows):
    """공지 제목에서 영향받는 업비트 코인 추정: '(ZK)' 같은 티커, 'Tron 네트워크 계열' 같은 네트워크명."""
    hits = []
    tickers = set(re.findall(r"\(([A-Z0-9]{2,12})\)", title))
    hits += [r for r in rows if r["currency"] in tickers]
    m = re.search(r"([A-Za-z0-9][A-Za-z0-9 .\-]*?)\s*네트워크", title)
    if m:
        name = m.group(1).strip().lower()
        hits += [r for r in rows if r["network_name"].lower() == name or r["net_type"].lower() == name]
    seen, out = set(), []
    for r in hits:
        k = (r["currency"], r["net_type"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def network_key(r):
    return f'{r["currency"]}@{r["net_type"]}'


# ---------- 페이지 ----------

def build_page(rows, now, main_url="index.html"):
    by_net = {}
    for r in rows:
        by_net.setdefault(r["network_name"], []).append(r)
    nets = sorted(by_net.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    coins = {r["currency"] for r in rows}
    multi = {}
    for r in rows:
        multi.setdefault(r["currency"], []).append(r["network_name"])
    multi = {c: n for c, n in multi.items() if len(n) > 1}

    def state_badge(r):
        s = r["state"]
        cls = "ok" if s == "working" else "bad" if s == "paused" else "mid"
        label = WALLET_STATE.get(s, s or "-")
        if r["block_state"] and r["block_state"] != "normal":
            label += " · 블록 지연" if r["block_state"] == "delayed" else f" · {r['block_state']}"
            cls = "mid" if cls == "ok" else cls
        return f'<span class="st {cls}">{html.escape(label)}</span>'

    def coin_row(r):
        name = html.escape(r["korean_name"] or r["english_name"] or "")
        mk = " ".join(f'<span class="mk">{m}</span>' for m in r["markets"]) or '<span class="mk off">마켓 없음</span>'
        warn = ' <span class="warn">유의</span>' if r["warning"] else ""
        search = html.escape(f'{r["currency"]} {r["korean_name"]} {r["english_name"]} {r["network_name"]}'.lower())
        return (f'<tr data-q="{search}"><td><b>{html.escape(r["currency"])}</b> {name}{warn}</td>'
                f'<td>{mk}</td><td>{state_badge(r)}</td>'
                f'<td class="num">{html.escape(str(r["withdraw_fee"] or "-"))}</td></tr>')

    nav = "".join(f'<a href="#n{i}">{html.escape(n)} <span>{len(rs)}</span></a>' for i, (n, rs) in enumerate(nets))
    sections = []
    for i, (n, rs) in enumerate(nets):
        rs = sorted(rs, key=lambda r: (not r["markets"], r["currency"]))
        sections.append(
            f'<section class="net" id="n{i}"><h2>{html.escape(n)} <span class="n">{len(rs)}개</span></h2>'
            f'<div class="tw"><table><thead><tr><th>코인</th><th>마켓</th><th>입출금</th><th>출금 수수료</th></tr></thead>'
            f'<tbody>{"".join(coin_row(r) for r in rs)}</tbody></table></div></section>')
    multi_html = "".join(f'<li><b>{html.escape(c)}</b> — {html.escape(", ".join(n))}</li>'
                         for c, n in sorted(multi.items()))

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>업비트 지원 네트워크</title>
<style>
:root{{--bg:#f7f7f5;--fg:#1c1c1c;--muted:#6b6b6b;--card:#fff;--line:#e4e4e0;--accent:#c2410c;
--ok:#15803d;--okbg:#dcfce7;--bad:#b91c1c;--badbg:#fee2e2;--mid:#a16207;--midbg:#fef3c7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eaeaea;--muted:#9a9a9a;--card:#1f1f1f;--line:#333;--accent:#fb923c;
--ok:#86efac;--okbg:#14321f;--bad:#fca5a5;--badbg:#3b1717;--mid:#fcd34d;--midbg:#3a2e0e}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,"Malgun Gothic",sans-serif;padding:24px 16px}}
main{{max-width:960px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);margin-bottom:16px}} .sub a{{color:var(--accent)}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;font-size:15px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}}
.nav{{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 8px}}
.nav a{{font-size:13px;padding:3px 9px;border:1px solid var(--line);border-radius:99px;background:var(--card);color:var(--fg);text-decoration:none}}
.nav a span{{color:var(--muted)}}
h2{{font-size:17px;margin:28px 0 8px}} .n{{font-size:13px;color:var(--muted);font-weight:normal}}
.tw{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{font-size:12.5px;color:var(--muted);font-weight:600}} tr:last-child td{{border-bottom:0}}
.num{{font-variant-numeric:tabular-nums}}
.mk{{font-size:11.5px;padding:1px 6px;border-radius:4px;background:var(--line);margin-right:3px}} .mk.off{{color:var(--muted)}}
.st{{font-size:12px;padding:2px 7px;border-radius:4px}}
.ok{{color:var(--ok);background:var(--okbg)}} .bad{{color:var(--bad);background:var(--badbg)}} .mid{{color:var(--mid);background:var(--midbg)}}
.warn{{font-size:11px;color:var(--bad);border:1px solid var(--bad);border-radius:4px;padding:0 4px}}
details{{margin-top:20px}} summary{{cursor:pointer;font-weight:600}} details ul{{font-size:14px}}
</style></head><body><main>
<h1>업비트 지원 네트워크</h1>
<div class="sub">{now:%Y년 %m월 %d일 %H:%M} 기준 · 코인 {len(coins)}개 · 네트워크 {len(nets)}개 · 출처: 업비트 출금 수수료 안내·입출금 현황 · <a href="{html.escape(main_url)}">← 뉴스 브리핑</a></div>
<input id="q" type="search" placeholder="코인·네트워크 검색 (예: USDT, 솔라나, Base)">
<div class="nav">{nav}</div>
<details><summary>여러 네트워크를 지원하는 코인 ({len(multi)}개)</summary><ul>{multi_html}</ul></details>
{"".join(sections)}
</main>
<script>
const q=document.getElementById('q');
q.addEventListener('input',()=>{{const v=q.value.trim().toLowerCase();
document.querySelectorAll('section.net').forEach(s=>{{let n=0;
s.querySelectorAll('tbody tr').forEach(tr=>{{const hit=!v||tr.dataset.q.includes(v)||s.querySelector('h2').textContent.toLowerCase().includes(v);tr.hidden=!hit;if(hit)n++;}});
s.hidden=n===0;}});}});
</script></body></html>"""


# ---------- 텔레그램 ----------

def notice_messages(notices, rows):
    msgs = []
    for n in notices:
        lines = [f'<b>📢 업비트 공지 · {html.escape(n.get("category", ""))}</b>',
                 f'<a href="{NOTICE_LINK.format(id=n["id"])}">{html.escape(n["title"])}</a>']
        hits = affected_coins(n["title"], rows)
        if hits:
            shown = ", ".join(f'{r["currency"]}({r["network_name"]})' for r in hits[:30])
            more = f" 외 {len(hits) - 30}개" if len(hits) > 30 else ""
            lines.append(f"영향 코인 {len(hits)}개: {html.escape(shown)}{more}")
        msgs.append("\n".join(lines))
    return msgs


def network_change_message(added, removed):
    if not added and not removed:
        return None
    lines = ["<b>🔀 업비트 지원 네트워크 변경</b>"]
    if added:
        lines.append("추가: " + html.escape(", ".join(f'{r["currency"]}({r["network_name"]})' for r in added)))
    if removed:
        lines.append("제외: " + html.escape(", ".join(removed)))
    return "\n".join(lines)
