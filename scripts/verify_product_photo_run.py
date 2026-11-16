"""Verify a uniform photo pilot without individual overrides or changed sources."""
import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

import process_product_photos as photos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=photos.DEFAULT_ARCHIVE)
    parser.add_argument('--run', required=True)
    parser.add_argument('--reference-run', help='Require exactly the same source set as an earlier pilot')
    args = parser.parse_args()
    for run in [args.run, args.reference_run]:
        if run and not re.fullmatch(r'local-[a-z0-9][a-z0-9-]*', run):
            parser.error('Invalid run name')
    root = args.archive.resolve()
    read_records = lambda run: [json.loads(p.read_text(encoding='utf-8')) for p in
                               sorted((root / 'reports' / run / 'records').rglob('*.json'))]
    records = read_records(args.run)
    failures, presets, sources, counts = [], set(), set(), {}
    for r in records:
        try:
            recipe = r['recipe']
            if recipe['mask_edits'] or recipe['override_sha256']:
                raise ValueError('Photo-specific override was used')
            presets.add(photos.dump(recipe['preset']))
            if r['source_path'] in sources:
                raise ValueError('More than one version of a source in this pilot')
            sources.add(r['source_path'])
            source = photos.original_path(root, r['source_path'])
            if photos.file_sha(source) != r['source_sha256']:
                raise ValueError('Original checksum changed')
            for output in r['outputs'].values():
                file = (root / output['path']).resolve()
                if not file.is_relative_to(root) or photos.file_sha(file) != output['sha256']:
                    raise ValueError('Output checksum/path mismatch')
            with Image.open(root / r['outputs']['master']['path']) as master_file:
                master = np.asarray(master_file.convert('RGBA'))
            if r['background_policy'] == 'preserve-source':
                original = photos.read_source(source)
                np.testing.assert_array_equal(master[:, :, :3], np.asarray(original))
                alpha = photos.read_source_alpha(source)
                expected = np.asarray(alpha) if alpha is not None else np.full(master.shape[:2], 255, np.uint8)
                np.testing.assert_array_equal(master[:, :, 3], expected)
            elif r['mask_origin'] == 'existing-source-transparency':
                np.testing.assert_array_equal(master[:, :, 3], np.asarray(photos.read_source_alpha(source)))
            reason = r.get('background_routing', {}).get('reason', r['mask_origin'])
            counts[reason] = counts.get(reason, 0) + 1
        except Exception as exc:
            failures.append({'source': r['source_path'], 'error': str(exc)})
    if not records or len(presets) != 1:
        failures.append({'error': 'Expected a nonempty run with exactly one shared preset'})
    if args.reference_run:
        previous = {r['source_path'] for r in read_records(args.reference_run)}
        if not previous or previous != sources:
            failures.append({'error': 'Source set differs from the reference pilot'})
    report = {'run': args.run, 'source_count': len(sources), 'shared_preset_count': len(presets),
              'reference_run': args.reference_run, 'routing_counts': counts, 'failures': failures,
              'note': 'Integrity and policy checks; not automatic visual approval.'}
    photos.atomic_write(root / 'reports' / args.run / 'regression-verification.json', photos.dump(report))
    print(photos.dump(report))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
