#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.core.i18n import language_scope, resolve_language, current_language, tr
from app.jobcenter.list_query_expansion import expand_list_query
from app.jobcenter.adaptive_search import AdaptiveSearch
from app.jobcenter.search_url import configured_search_url, parse_search_url, result_window_limit


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview logical -> physical list query expansion.")
    parser.add_argument("--load-env", action="store_true", help="Load environment variables from .env if present.")
    parser.add_argument('--output-language', choices=('ru', 'de'), help='CLI > ENV > app JSON > ru')
    args = parser.parse_args()

    if args.load_env:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

    cfg = load_app_config()
    with language_scope(resolve_language(cfg, cli=args.output_language)):
        list_api = cfg['jobcenter']['list_api']
        url = configured_search_url(list_api)
        if url:
            search = parse_search_url(url, list_api.get('params', {}))
            result = AdaptiveSearch(search, list_api, result_window_limit(list_api)).preview()
            result['note'] = tr(result['note'])
        else:
            result = expand_list_query(list_api).to_dict()
        result['output_language'] = current_language()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
