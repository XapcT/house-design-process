"""Publish the builder's actual intermediate spec to the shared House Design store."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'house-design-app'))
import design_session as sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request-id', help='Reuse only when retrying the exact same operation')
    commands = parser.add_subparsers(dest='command', required=True)
    begin = commands.add_parser('begin')
    begin.add_argument('title')
    publish = commands.add_parser('publish')
    publish.add_argument('session_id')
    publish.add_argument('spec', type=Path)
    publish.add_argument('--revision', default=None, help='Latest returned revision; omit only for first publication')
    publish.add_argument('--phase', required=True)
    phase = commands.add_parser('phase')
    phase.add_argument('session_id')
    phase.add_argument('phase')
    phase.add_argument('--state', choices=('designing', 'waiting', 'blocked'), default='designing')
    finish = commands.add_parser('finish')
    finish.add_argument('session_id')
    finish.add_argument('--status', choices=('finished', 'failed', 'cancelled', 'interrupted'), default='finished')
    args = parser.parse_args(argv)
    request = args.request_id or str(uuid.uuid4())
    if args.command == 'begin':
        state = sessions.begin_design(ROOT, args.title, request)
    elif args.command == 'publish':
        raw = args.spec.read_bytes()
        if len(raw) > sessions.MAX_SPEC_BYTES:
            parser.error('Spec exceeds the bounded session payload limit')
        state = sessions.update_design(ROOT, args.session_id, request, args.revision, args.phase,
                                       spec=json.loads(raw))
    elif args.command == 'phase':
        state = sessions.set_phase(ROOT, args.session_id, request, args.phase, args.state)
    else:
        state = sessions.finish_design(ROOT, args.session_id, request, args.status)
    print(json.dumps({'request_id': request, **state}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
