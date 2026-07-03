"""Parse OSM import result and summarize buildings."""
import json, sys

with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)

if 'detail' in d:
    print('ERROR:', d['detail'])
    sys.exit(1)

print(f"Success: {d['num_buildings']} buildings")
print(f"BBox: {d['bbox']}")
print()

feats = d['plan']['features']
type_counts = {}
for f in feats:
    p = f['properties']
    bt = p['building_type']
    type_counts[bt] = type_counts.get(bt, 0) + 1
    n = (p.get('name') or '?')[:35]
    h = p.get('height', '?')
    nf = p.get('num_floors', '?')
    print(f"  {n:<37s} | {bt:<12s} | h={h:>5}m | {nf}f")

print(f"\nType summary:")
for bt, c in sorted(type_counts.items(), key=lambda x: -x[1]):
    print(f"  {bt}: {c}")
print(f"  TOTAL: {len(feats)}")

# Save the plan for later use
with open('/tmp/osm_plan.json', 'w', encoding='utf-8') as f:
    json.dump(d['plan'], f, ensure_ascii=False, indent=2)
print("\nPlan saved to /tmp/osm_plan.json")
