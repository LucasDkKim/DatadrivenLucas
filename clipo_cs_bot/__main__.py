"""CLI 진입점.

  python -m clipo_cs_bot run                 # Socket Mode 로 실시간 감시 (기본)
  python -m clipo_cs_bot poll                # 폴링 모드
  python -m clipo_cs_bot draft --ts <ts>     # 특정 문의로 초안만 생성해 터미널에 출력 (슬랙에 안 올림)
  python -m clipo_cs_bot draft --ts <ts> --post   # 생성 후 스레드에 게시
  python -m clipo_cs_bot backfill --hours 24 # 최근 N시간 내 미응대 문의 일괄 처리
  python -m clipo_cs_bot guide               # 가이드가 어디서/어떻게 로드되는지 확인
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

from .config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipo_cs_bot", description="클리포 고객센터 1차 응대 도우미")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Socket Mode 로 실시간 감시")
    sub.add_parser("poll", help="폴링 모드로 감시")

    p_draft = sub.add_parser("draft", help="특정 메시지(ts)로 초안 생성")
    p_draft.add_argument("--ts", required=True, help="Slack 메시지 ts (예: 1788776946.730899)")
    p_draft.add_argument("--post", action="store_true", help="생성한 초안을 스레드에 게시")

    p_back = sub.add_parser("backfill", help="최근 N시간 내 미응대 문의 일괄 처리")
    p_back.add_argument("--hours", type=float, default=24)

    sub.add_parser("guide", help="가이드 로드 확인")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    command = args.command or "run"

    if command == "guide":
        from .guide import GuideStore

        settings = load_settings(require_slack=False, require_claude=False)
        store = GuideStore(settings)
        text = store.get(force_refresh=True)
        print(f"# 출처: {store.source} / {len(text)}자\n")
        print(text[:3000] + ("\n...(생략)" if len(text) > 3000 else ""))
        return 0

    settings = load_settings()

    if command == "run":
        from .slack_app import run_socket_mode

        run_socket_mode(settings)
        return 0

    if command == "poll":
        from .slack_app import run_polling

        run_polling(settings)
        return 0

    from slack_sdk import WebClient

    from .slack_app import InquiryProcessor

    client = WebClient(token=settings.slack_bot_token)

    if command == "draft":
        if not args.post:
            settings = dataclasses.replace(settings, dry_run=True)
        processor = InquiryProcessor(settings, client)
        ok = processor.process_ts(args.ts, force=True)
        return 0 if ok else 1

    if command == "backfill":
        processor = InquiryProcessor(settings, client)
        n = processor.scan_pending(since_hours=args.hours)
        print(f"처리 완료: {n}건")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
