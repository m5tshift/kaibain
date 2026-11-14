import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from kaibain.store import Store


def main():
    parser = argparse.ArgumentParser(description='Kaibain — local tasks for humans and agents')
    parser.add_argument('mode', nargs='?', default='web', choices=['start', 'status', 'stop', 'web', 'mcp', 'agent-job','backup','backup-inspect','restore'])
    parser.add_argument('--run-id', help=argparse.SUPPRESS)
    parser.add_argument('--db', help='Absolute path to a shared SQLite file (default: project/data/kaibain.sqlite3)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--archive',help='Backup archive to inspect or restore')
    parser.add_argument('--output',help='New backup archive path')
    args = parser.parse_args()
    if args.mode in ('start', 'status', 'stop'):
        from kaibain.launcher import manage
        return manage(args.mode, args.port, args.db)
    if args.mode in ('backup','backup-inspect','restore'):
        from kaibain.backup import create_backup,inspect_backup,restore_backup
        from kaibain.store import DomainError
        try:
            if args.mode=='backup':
                if not args.output:parser.error('backup requires --output')
                database=Path(args.db).resolve() if args.db else Path(__file__).resolve().parent/'data/kaibain.sqlite3'
                value=create_backup(database,Path(args.output))
            elif args.mode=='backup-inspect':
                if not args.archive:parser.error('backup-inspect requires --archive')
                value=inspect_backup(Path(args.archive))
            else:
                if not args.archive or not args.db:parser.error('restore requires --archive and --db for a new location')
                value=restore_backup(Path(args.archive),Path(args.db))
            print(json.dumps({'ok':True,'result':value},ensure_ascii=True))
        except (DomainError,OSError,ValueError) as error:
            print(json.dumps({'ok':False,'error':str(error),'code':getattr(error,'code','BACKUP_FAILED')},ensure_ascii=True))
            raise SystemExit(2)
        return
    store = Store(args.db)
    if args.mode == 'agent-job':
        from kaibain.agent_runtime import run_job
        run_job(store, args.run_id)
    elif args.mode == 'mcp':
        from kaibain.mcp_server import build_mcp
        build_mcp(store).run(transport='stdio')
    else:
        from kaibain.web import run
        run(store, args.port)


if __name__ == '__main__':
    main()
