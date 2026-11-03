# 코인 기술·보안 브리핑

보안 사고, 하드포크, 네트워크 업그레이드, 네트워크 장애 뉴스만 골라 텔레그램으로 보내고,
전체 리포트(영문 기사는 한국어 번역)를 GitHub Pages에 올립니다.
GitHub 서버가 실행하므로 PC를 켜 둘 필요가 없습니다.

- **약 30분마다**: 지난 실행 이후 새로 잡힌 기사만 `🔔 새 소식` 알림 (없으면 조용히 넘어감)
- **매일 08:00 KST**: 최근 24시간 하루 요약
- **업비트**: 지원 코인별 네트워크 페이지(`upbit.html`, 출금 수수료 안내·입출금 현황 기준)를 만들고,
  입출금 중단·네트워크 업그레이드·거래지원 종료 등 새 공지와 지원 네트워크 변경을 영향 코인과 함께 알림
- 이미 알린 기사·번역 결과는 Actions 캐시(`state.json`)에 저장해 중복 알림과 번역 재요청을 막습니다.
  첫 실행은 기존 기사를 기록만 하고 알림을 보내지 않습니다.

## 1. 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 봇 이름과 아이디(`..._bot`) 입력
2. 받은 **토큰**(`123456:ABC...`)을 메모
3. 방금 만든 봇을 찾아 **아무 메시지나 한 번 보내기** (그래야 봇이 나에게 보낼 수 있음)
4. 이 폴더에 `telegram.env` 파일을 만들고 토큰을 넣기 (이 파일은 git에 올라가지 않습니다)
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   ```
5. 내 chat id 확인:
   ```
   python coin_news.py --get-chat-id
   ```
   출력된 `TELEGRAM_CHAT_ID=...` 줄을 `telegram.env`에 추가
6. 로컬에서 전송 테스트:
   ```
   python coin_news.py --hours 24 --telegram
   ```

## 2. GitHub에 올리기

1. GitHub에서 새 저장소 만들기 (예: `CoinNews`)
   - GitHub Pages를 무료로 쓰려면 **Public** 저장소여야 합니다. 코드에 비밀 정보는 없습니다.
2. 이 폴더를 저장소에 push
   ```
   git init
   git add .
   git commit -m "Coin news brief"
   git branch -M main
   git remote add origin https://github.com/<내아이디>/CoinNews.git
   git push -u origin main
   ```
3. 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 두 개 등록
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. **Settings → Pages → Source** 를 **GitHub Actions** 로 변경
5. **Actions** 탭 → *Daily coin news* → **Run workflow** 로 한 번 수동 실행해 확인

이후 매일 08:00 KST에 자동 실행됩니다. (GitHub 예약 실행은 수 분~수십 분 늦어질 수 있습니다.)
전체 리포트 주소: `https://<내아이디>.github.io/CoinNews/`

## 설정 바꾸기

- 실행 시각: `.github/workflows/daily-news.yml`의 `cron` (UTC 기준, KST = UTC+9)
- 뉴스 출처: `coin_news.py`의 `FEEDS`
- 키워드: `coin_news.py`의 `CATEGORIES`
- 텔레그램에 카테고리당 보여줄 기사 수: `build_telegram_messages`의 `per_cat`
