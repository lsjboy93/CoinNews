"""코인 기술/보안 뉴스 브리핑.

여러 크립토 뉴스 RSS를 모아 보안 사고, 하드포크, 네트워크 업그레이드, 장애 관련
기사만 골라 HTML 리포트로 저장하고 브라우저로 엽니다. 표준 라이브러리만 사용합니다.

사용법:
    python coin_news.py            # 최근 48시간 기사
    python coin_news.py --hours 24 # 최근 24시간 기사
    python coin_news.py --no-open  # 브라우저 열지 않기 (예약 실행용)
    python coin_news.py --telegram # 텔레그램으로 요약 전송
    python coin_news.py --telegram new  # 지난 실행 이후 새로 잡힌 기사만 알림
    python coin_news.py --get-chat-id  # 봇에게 메시지 보낸 뒤 내 chat id 확인

텔레그램 설정은 환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 또는
스크립트 옆 telegram.env 파일(KEY=VALUE 형식)에서 읽습니다.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

import upbit
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
    "The Defiant": "https://thedefiant.io/api/feed",
    "블록미디어": "https://www.blockmedia.co.kr/feed",
    "코인데스크코리아": "https://www.coindeskkorea.com/feed",
}

# 카테고리별 키워드 (정규식, 대소문자 무시). 위에 있는 카테고리가 우선 적용됩니다.
CATEGORIES = {
    "🚨 보안 사고": [
        r"\bhack(ed|er|ers|s)?\b", r"\bexploit(ed|s)?\b", r"\bdrain(ed|er)?\b",
        r"\bstolen\b", r"\btheft\b", r"\bbreach\b", r"\bvulnerabilit(y|ies)\b",
        r"\bbug bounty\b", r"\bphishing\b", r"\bcompromis(e|ed)\b", r"\battack(er|ers|ed)?\b",
        r"\bflash loan\b", r"\brug ?pull\b", r"\bprivate key", r"\bCVE-\d+",
        r"\bsecurity (incident|flaw|issue)\b", r"\blazarus\b", r"\bfroze?n? funds\b",
        "해킹", "해커", "탈취", "취약점", "익스플로잇", "유출", "피싱", "도난", "공격",
    ],
    "🍴 하드포크": [
        r"\bhard ?fork", r"\bsoft ?fork", r"\bchain split\b", r"\bfork(ed|s)?\b",
        "하드포크", "소프트포크", "포크",
    ],
    "⬆️ 네트워크 업그레이드": [
        r"\bupgrade[sd]?\b", r"\bmainnet\b", r"\btestnet\b", r"\bactivation\b",
        r"\bEIP-\d+", r"\bBIP-?\d+", r"\bSIMD-\d+", r"\bimprovement proposal\b",
        r"\bPectra\b", r"\bFusaka\b", r"\bGlamsterdam\b", r"\bFiredancer\b",
        r"\bAlpenglow\b", r"\bprotocol (update|change)\b", r"\bnode (operators?|release|software)\b",
        r"\bclient release\b", r"\bv\d+\.\d+(\.\d+)? release",
        "업그레이드", "메인넷", "테스트넷", "개선 제안", "활성화",
    ],
    "⚠️ 네트워크 장애": [
        r"\boutage\b", r"\bhalt(ed|s)?\b", r"\bdowntime\b", r"\bstall(ed|s)?\b",
        r"\bconsensus (failure|bug|issue)\b", r"\breorg\b", r"\bnetwork (down|issue|disruption)\b",
        r"\bpaused? (deposits|withdrawals)\b", r"\bsuspend(ed|s)? (deposits|withdrawals)\b",
        "장애", "중단", "멈춤", "입출금 중단", "입출금 일시",
    ],
}

COMPILED = {cat: re.compile("|".join(pats), re.IGNORECASE) for cat, pats in CATEGORIES.items()}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CoinNewsBrief/1.0"
OUT_DIR = Path(__file__).resolve().parent / "reports"


def fetch(name, url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return name, resp.read(), None
    except Exception as e:  # 피드 하나가 실패해도 나머지는 계속
        return name, None, str(e)


def strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(source, raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 일부 피드는 이스케이프 안 된 '&'나 제어문자를 포함함 → 정리 후 재시도
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"^<\?xml[^>]*\?>", "", text.lstrip())
        root = ET.fromstring(text)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    for it in items:
        def get(tag):
            el = it.find(tag)
            if el is None:
                el = it.find(atom + tag)
            return el

        title = strip_tags((get("title").text if get("title") is not None else "") or "")
        link_el = get("link")
        link = ""
        if link_el is not None:
            link = (link_el.text or link_el.get("href") or "").strip()
        desc_el = get("description")
        if desc_el is None:
            desc_el = get("summary")
        desc = strip_tags(desc_el.text if desc_el is not None else "")
        date_el = get("pubDate")
        if date_el is None:
            date_el = get("published")
        if date_el is None:
            date_el = get("updated")
        published = parse_date(date_el.text if date_el is not None else None)
        yield {"source": source, "title": title, "link": link, "desc": desc, "published": published}


def classify(article):
    # 제목 매칭을 우선, 없으면 요약문으로
    for text in (article["title"], article["desc"][:400]):
        for cat, rx in COMPILED.items():
            m = rx.search(text)
            if m:
                return cat, m.group(0)
    return None, None


def short_desc(text, n=220):
    return text[:n] + ("…" if len(text) > n else "")


HANGUL = re.compile("[가-힣]")


TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ko&dt=t"


def _translate_request(text):
    """Google 번역 웹 엔드포인트(무료, 키 불필요). 429(요청 과다)면 잠시 쉬고 재시도."""
    body = urllib.parse.urlencode({"q": text}).encode()
    for wait in (0, 3, 10):
        time.sleep(wait)
        try:
            req = urllib.request.Request(TRANSLATE_URL, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return "".join(seg[0] for seg in data[0] if seg and seg[0]).replace("​", "")
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return None
        except Exception:
            return None
    return None


def translate_batch(texts, max_chars=1800):
    """여러 문장을 줄바꿈으로 묶어 한 번에 번역 (요청 수를 줄여 차단을 피함). {원문: 번역}"""
    texts = [t.replace("\n", " ").strip() for t in texts if t and not HANGUL.search(t)]
    chunks, cur = [], []
    for t in texts:
        if cur and sum(len(x) + 1 for x in cur) + len(t) > max_chars:
            chunks.append(cur)
            cur = []
        cur.append(t)
    if cur:
        chunks.append(cur)

    result, requests = {}, 0
    for chunk in chunks:
        requests += 1
        out = _translate_request("\n".join(chunk))
        if out is None:
            break  # 차단되면 이번 실행은 여기까지 (다음 실행 때 이어서)
        lines = [s.strip() for s in out.split("\n")]
        if len(lines) == len(chunk):
            result.update({src: ko for src, ko in zip(chunk, lines) if ko})
        else:  # 줄 수가 어긋나면 한 문장씩
            for src in chunk:
                requests += 1
                ko = _translate_request(src)
                if ko:
                    result[src] = ko.strip()
        time.sleep(0.5)
    return result, requests


def translate_articles(grouped, cache):
    """영문 기사의 제목·요약을 번역해 title_ko / desc_ko 에 저장. 번역 실패 시 원문 유지.

    cache(원문 → 번역)에 있는 문장은 다시 요청하지 않고, 이번에 쓴 번역만 담은 새 cache를 돌려줍니다.
    """
    arts = [a for v in grouped.values() for a in v if not HANGUL.search(a["title"])]

    def src(text):
        return text.replace("\n", " ").strip()

    texts = []
    for a in arts:
        texts.append(src(a["title"]))
        if a["desc"]:
            texts.append(src(short_desc(a["desc"])))
    todo = list(dict.fromkeys(t for t in texts if t not in cache))
    fresh, requested = translate_batch(todo) if todo else ({}, 0)
    known = {**cache, **fresh}

    for a in arts:
        a["title_ko"] = known.get(src(a["title"]))
        if a["desc"]:
            a["desc_ko"] = known.get(src(short_desc(a["desc"])))
    done = sum(1 for a in arts if a.get("title_ko"))
    print(f"  [번역] {done}/{len(arts)}건 번역 (새 요청 {requested}건)")
    return {t: known[t] for t in texts if t in known}


def load_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        state = {}
    state.setdefault("seen", {})
    state.setdefault("tr", {})
    return state


def save_state(path, state, now, keep_days=4):
    cutoff = (now - timedelta(days=keep_days)).isoformat()
    state["seen"] = {k: t for k, t in state["seen"].items() if t >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def build_html(grouped, errors, hours, now, upbit_link=False):
    total = sum(len(v) for v in grouped.values())
    sections = []
    for cat in CATEGORIES:
        arts = grouped.get(cat, [])
        if not arts:
            continue
        rows = []
        for a in arts:
            when = a["published"].astimezone().strftime("%m/%d %H:%M") if a["published"] else "-"
            desc = html.escape(a.get("desc_ko") or short_desc(a["desc"]))
            orig = f'<div class="orig">{html.escape(a["title"])}</div>' if a.get("title_ko") else ""
            rows.append(
                f'<li><a href="{html.escape(a["link"])}" target="_blank">{html.escape(a.get("title_ko") or a["title"])}</a>'
                f'{orig}<div class="meta">{html.escape(a["source"])} · {when} · '
                f'<span class="kw">{html.escape(a["keyword"])}</span></div>'
                f'<p>{desc}</p></li>'
            )
        sections.append(f"<section><h2>{cat} <span class=n>{len(arts)}</span></h2><ul>{''.join(rows)}</ul></section>")
    if not sections:
        sections.append("<p class=empty>해당 기간에 관련 기사가 없습니다.</p>")
    err_html = ""
    if errors:
        err_html = "<details><summary>불러오지 못한 피드</summary><ul>" + "".join(
            f"<li>{html.escape(n)}: {html.escape(e)}</li>" for n, e in errors) + "</ul></details>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>코인 기술·보안 브리핑 {now:%Y-%m-%d}</title>
<style>
:root{{--bg:#f7f7f5;--fg:#1c1c1c;--muted:#6b6b6b;--card:#fff;--line:#e4e4e0;--accent:#c2410c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eaeaea;--muted:#9a9a9a;--card:#1f1f1f;--line:#333;--accent:#fb923c}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,"Malgun Gothic",sans-serif;padding:24px 16px}}
main{{max-width:860px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);margin-bottom:24px}} .sub a{{color:var(--accent)}}
h2{{font-size:17px;border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:32px}}
.n{{font-size:13px;color:var(--muted);font-weight:normal}}
ul{{list-style:none;padding:0;margin:0}}
li{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}}
li a{{color:var(--fg);font-weight:600;text-decoration:none}} li a:hover{{color:var(--accent)}}
.meta{{color:var(--muted);font-size:12.5px;margin-top:3px}}
.kw{{color:var(--accent)}} .orig{{color:var(--muted);font-size:12.5px;margin-top:2px}}
li p{{margin:6px 0 0;font-size:13.5px;color:var(--muted)}}
details{{margin-top:32px;color:var(--muted);font-size:13px}} .empty{{color:var(--muted)}}
</style></head><body><main>
<h1>코인 기술·보안 브리핑</h1>
<div class="sub">{now:%Y년 %m월 %d일 %H:%M} 기준 · 최근 {hours}시간 · 관련 기사 {total}건{' · <a href="upbit.html">업비트 지원 네트워크 →</a>' if upbit_link else ''}</div>
{''.join(sections)}
{err_html}
</main></body></html>"""


def load_telegram_config():
    cfg = {}
    env_file = Path(__file__).resolve().parent / "telegram.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("TELEGRAM_CHAT_ID")
    return token, chat_id


def telegram_api(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def build_telegram_messages(grouped, header, report_url, per_cat=8):
    """카테고리별 상위 기사만 담은 메시지 목록 (텔레그램 4096자 제한에 맞춰 분할)."""
    blocks = []
    for cat in CATEGORIES:
        arts = grouped.get(cat, [])
        if not arts:
            continue
        lines = [f"\n<b>{cat}</b> ({len(arts)}건)"]
        for a in arts[:per_cat]:
            lines.append(f'• <a href="{html.escape(a["link"])}">{html.escape(a.get("title_ko") or a["title"])}</a>'
                         f' <i>{html.escape(a["source"])}</i>')
        if len(arts) > per_cat:
            lines.append(f"  … 외 {len(arts) - per_cat}건")
        blocks.append("\n".join(lines))
    if not blocks:
        blocks.append("\n해당 기간에 관련 기사가 없습니다.")
    footer = f'\n\n<a href="{html.escape(report_url)}">전체 리포트 보기</a>' if report_url else ""

    messages, cur = [], header
    for b in blocks:
        if len(cur) + len(b) > 3800:
            messages.append(cur)
            cur = b.lstrip("\n")
        else:
            cur += "\n" + b
    messages.append(cur + footer)
    return messages


def send_telegram(grouped, header, report_url):
    return send_messages(build_telegram_messages(grouped, header, report_url))


def send_messages(messages):
    """전송했거나 설정이 없어 건너뛰면 True, 전송 중 오류면 False."""
    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        print("  [텔레그램] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않아 건너뜀", file=sys.stderr)
        return True
    try:
        for msg in messages:
            telegram_api(token, "sendMessage", {
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
    except Exception as e:
        print(f"  [텔레그램] 전송 실패: {e}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Telegram send failed::{e}")
        return False
    print("  [텔레그램] 전송 완료")
    return True


def print_chat_ids():
    token, _ = load_telegram_config()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN 이 필요합니다 (환경변수 또는 telegram.env).")
    updates = telegram_api(token, "getUpdates").get("result", [])
    chats = {}
    for u in updates:
        chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name", "")
    if not chats:
        print("메시지가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
    for cid, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({name})")


def main():
    ap = argparse.ArgumentParser(description="코인 보안/하드포크/업그레이드 뉴스 브리핑")
    ap.add_argument("--hours", type=int, default=48, help="몇 시간 이내 기사만 볼지 (기본 48)")
    ap.add_argument("--no-open", action="store_true", help="리포트를 브라우저로 열지 않음")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="리포트 저장 폴더 (기본: 스크립트 옆 reports)")
    ap.add_argument("--telegram", nargs="?", const="digest", choices=["digest", "new"],
                    help="텔레그램 전송: digest=전체 요약(기본), new=지난 실행 이후 새 기사만")
    ap.add_argument("--state", type=Path, default=Path(__file__).resolve().parent / "state.json",
                    help="이미 본 기사·번역 캐시 저장 파일")
    ap.add_argument("--report-url", default=os.environ.get("REPORT_URL", ""),
                    help="텔레그램 메시지에 붙일 전체 리포트 주소")
    ap.add_argument("--get-chat-id", action="store_true", help="봇에게 온 메시지에서 chat id 출력")
    ap.add_argument("--no-translate", action="store_true", help="영문 기사 한국어 번역 끄기")
    ap.add_argument("--upbit", action="store_true",
                    help="업비트 지원 네트워크 페이지(upbit.html) 생성, 새 공지·네트워크 변경 알림")
    args = ap.parse_args()

    if args.get_chat_id:
        print_chat_ids()
        return

    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=args.hours)

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda kv: fetch(*kv), FEEDS.items()))

    grouped, errors, seen = {}, [], set()
    for name, raw, err in results:
        if err:
            errors.append((name, err))
            continue
        try:
            articles = list(parse_feed(name, raw))
        except ET.ParseError as e:
            errors.append((name, f"파싱 실패: {e}"))
            continue
        for a in articles:
            if a["published"] and a["published"] < cutoff:
                continue
            key = a["title"].lower()
            if not a["title"] or key in seen:
                continue
            cat, kw = classify(a)
            if not cat:
                continue
            seen.add(key)
            a["keyword"] = kw
            grouped.setdefault(cat, []).append(a)

    oldest = datetime.min.replace(tzinfo=timezone.utc)
    for arts in grouped.values():
        arts.sort(key=lambda a: a["published"] or oldest, reverse=True)

    state = load_state(args.state)
    if not args.no_translate:
        state["tr"] = translate_articles(grouped, state["tr"])

    args.out.mkdir(parents=True, exist_ok=True)
    upbit_ok = run_upbit(args, state, now) if args.upbit else True
    out = args.out / f"{now:%Y-%m-%d}.html"
    upbit_page = args.upbit and (args.out / "upbit.html").exists()
    out.write_text(build_html(grouped, errors, args.hours, now, upbit_link=upbit_page), encoding="utf-8")

    total = sum(len(v) for v in grouped.values())
    print(f"관련 기사 {total}건 → {out}")
    for cat in CATEGORIES:
        if grouped.get(cat):
            print(f"  {cat}: {len(grouped[cat])}건")
    for name, err in errors:
        print(f"  [실패] {name}: {err}", file=sys.stderr)

    def key(a):
        return a["link"] or a["title"].lower()

    first_run = not state["seen"]
    ok = True
    if args.telegram == "digest":
        header = f"<b>📰 코인 기술·보안 브리핑</b>\n{now:%Y-%m-%d %H:%M} · 최근 {args.hours}시간 · {total}건"
        ok = send_telegram(grouped, header, args.report_url)
    elif args.telegram == "new":
        fresh = {c: [a for a in v if key(a) not in state["seen"]] for c, v in grouped.items()}
        fresh = {c: v for c, v in fresh.items() if v}
        n = sum(len(v) for v in fresh.values())
        if first_run:
            print(f"  [텔레그램] 첫 실행: 기존 기사 {n}건을 기록만 하고 알림은 보내지 않음")
        elif n:
            ok = send_telegram(fresh, f"<b>🔔 새 소식 {n}건</b>", args.report_url)
        else:
            print("  [텔레그램] 새 기사 없음")

    if ok:  # 전송에 실패하면 기록하지 않아 다음 실행 때 다시 보냄
        stamp = now.isoformat()
        for a in (a for v in grouped.values() for a in v):
            state["seen"].setdefault(key(a), stamp)
    save_state(args.state, state, now)

    if not args.no_open:
        webbrowser.open(out.as_uri())
    if not (ok and upbit_ok):
        sys.exit(1)


def run_upbit(args, state, now):
    """업비트 지원 네트워크 페이지 생성 + 새 공지·네트워크 변경 알림. 실패하면 False."""
    try:
        rows = upbit.fetch_networks()
        notices = upbit.fetch_notices()
    except Exception as e:
        # 업비트 실패로 뉴스 리포트·알림까지 막히지 않도록 경고만 남김 (GitHub Actions 주석으로도 표시)
        print(f"  [업비트] 불러오기 실패: {e}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning title=Upbit fetch failed::{e}")
        return True

    page = args.out / "upbit.html"
    page.write_text(upbit.build_page(rows, now), encoding="utf-8")
    print(f"업비트 코인·네트워크 {len(rows)}개 → {page}")

    first_run = "upbit_notices" not in state
    seen_ids = set(state.get("upbit_notices", []))
    new_notices = [n for n in notices if n["id"] not in seen_ids and upbit.is_relevant_notice(n)]
    new_notices.sort(key=lambda n: n.get("listed_at", ""))

    current = {upbit.network_key(r): r for r in rows}
    prev = set(state.get("upbit_networks", []))
    added = [r for k, r in current.items() if prev and k not in prev]
    removed = sorted(k.replace("@", "(") + ")" for k in prev - current.keys())

    ok = True
    if args.telegram and not first_run:
        msgs = upbit.notice_messages(new_notices, rows)
        change = upbit.network_change_message(added, removed)
        if change:
            msgs.append(change)
        if msgs:
            ok = send_messages(msgs)
        print(f"  [업비트] 새 공지 {len(new_notices)}건, 네트워크 추가 {len(added)} / 제외 {len(removed)}")
    elif first_run:
        print(f"  [업비트] 첫 실행: 공지 {len(notices)}건을 기록만 하고 알림은 보내지 않음")

    if ok:
        state["upbit_notices"] = sorted(seen_ids | {n["id"] for n in notices})[-300:]
        state["upbit_networks"] = sorted(current)
    return ok


if __name__ == "__main__":
    main()
