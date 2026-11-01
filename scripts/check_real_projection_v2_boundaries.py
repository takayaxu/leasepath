"""Reject damaged copies of a completed real run; never edit source evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_projection_v2 import export_v2

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--clients', type=Path, nargs='+', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
results = []
for case in ['missing_producer', 'missing_terminal', 'mixed_boot',
             'missing_rank_checkpoint', 'undrained_engine_checkpoint']:
    with tempfile.TemporaryDirectory(prefix='leasepath-boundary-') as temporary:
        run = Path(temporary) / args.run.name
        shutil.copytree(args.run / 'raw', run / 'raw')
        for name in ['expected-streams.json', 'service-manifest.json']:
            shutil.copy2(args.run / name, run / name)
        stream = sorted((run / 'raw').glob('*.jsonl'))[0]
        if case == 'missing_producer':
            stream.unlink()
        elif case == 'missing_terminal':
            stream.write_bytes(b''.join(stream.read_bytes().splitlines(keepends=True)[:-1]))
        else:
            # Keep this stream individually valid, to specifically test the
            # cross-producer boot boundary rather than a corrupted hash.
            if case != 'mixed_boot':
                kind = ('rank_checkpoint' if case == 'missing_rank_checkpoint'
                        else 'engine_checkpoint')
                stream = next(p for p in (run / 'raw').glob('*.jsonl')
                              if any(json.loads(line)['kind'] == kind
                                     for line in p.read_text().splitlines()))
            rows = [json.loads(line) for line in stream.read_text().splitlines()]
            if case == 'missing_rank_checkpoint':
                rows = [row for row in rows if row['kind'] != 'rank_checkpoint']
            digest = hashlib.sha256()
            with stream.open('wb') as destination:
                for seq, row in enumerate(rows):
                    row['seq'] = seq
                    if case == 'mixed_boot':
                        row['boot_id'] = 'negative-test-other-boot'
                    elif case == 'undrained_engine_checkpoint' and row['kind'] == 'engine_checkpoint':
                        row['data']['outstanding_requests'] = 1
                    if row['kind'] == 'observer_end':
                        row['data']['prior_records'] = seq
                        row['data']['prior_sha256'] = digest.hexdigest()
                    encoded = (json.dumps(row, separators=(',', ':')) + '\n').encode()
                    destination.write(encoded)
                    digest.update(encoded)
        try:
            export_v2(run, args.clients, Path(temporary) / 'invalid-export')
        except ValueError as error:
            results.append({'case': case, 'rejected': True, 'reason': str(error)})
        else:
            raise AssertionError(f'accepted invalid boundary: {case}')
args.output.write_text(json.dumps({'status': 'passed', 'results': results}, indent=2) + '\n')
print(json.dumps(results))
