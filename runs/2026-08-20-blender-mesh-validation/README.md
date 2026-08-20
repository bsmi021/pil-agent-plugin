# Track B1 -- pil_blender_mesh validation and geometry.\* unlock

Evidence bundle for the Phase 3 Track B1 build: a Blender-scene wrapper
(`scripts/pil_blender_mesh.py`) plus the geometry.\* predicate unlock inside
`scripts/pil_contract_verdict.py`. All probes were run against the swordsman
corpus at `C:/Projects/tms-heim/art/skeleton-crusaders/swordsman/` with Blender
5.1.2 on Windows 11.

Files in `probes/`:

- `rev2-lower.json` -- probe of `runs/checkpoint-rev2-lower-20260818.blend`.
- `rev3-faceting.json` -- probe of `runs/checkpoint-rev3-faceting-20260818.blend`.
- `source.json` -- probe of `source/SM_Chr_Skeleton_BlackOrderSwordsman_01.blend`.
- `roundtrip.json` -- probe of `staging/roundtrip/SM_Chr_Skeleton_BlackOrderSwordsman_01.roundtrip.blend`.

Every pair fed to the topology check below is chronological (source → roundtrip,
rev2 → rev3), so item 5 of the handoff brief ("label reverse-ordered pairs
explicitly") is n/a for this bundle.

## 1. Tabard verification (docs/phase3-handoff.md §6 confirmed)

The handoff notes `SKS_Garment_Tabard_01` went from 1350 → 787 polys between
rev2-lower and rev3-faceting, a ~42% decrease. Probes agree exactly:

| revision  | polys | verts |
|-----------|-------|-------|
| rev2      | 1350  | 1584  |
| rev3      | 787   | 928   |

787 / 1350 = 0.583, i.e. -41.7%, which rounds to the -42% the doc quotes.
Pinned by `tests/test_blender_mesh.py::TestCorpusRealBlender::test_rev*_tabard_matches_docs_handoff_figure`.

## 2. Whole-model discrepancy resolved

The handoff also quotes a whole-model figure of **6,643 → 14,033 polys**
between rev2-lower and rev3-faceting. Summing `polys` across every entry in
each probe's `scene.mesh_objects` gives **9,120 → 16,276**. This is a real
scope difference, not a rounding error, and the handoff's numbers came from
`parts.json`, not the `.blend` scene.

Reconciliation (verified by summing the actual files):

| source                 | rev2 total | rev3 total | object count             |
|------------------------|------------|------------|--------------------------|
| this tool (scene)      | 9,120      | 16,276     | 19 / 19                  |
| corpus `parts.json`    | 6,643      | 14,033     | 17 / 18                  |

The gap:

- `parts.json` omits `SKS_Donor_BodyBelowChin_01` (2,243 polys, present in the
  scene at both revs). This is the largest single object.
- `parts.json` also omits `SKS_Head_Skull_01` (236 polys) at rev2, but adds it
  back at rev3 -- so the *tracked-part set* is itself unstable across revs.
- `parts.json` rev2 lists both feet at 164 polys; the scene reads 163 each,
  which is a 2-poly stale-sidecar residual.

Arithmetic closes exactly:

```
rev2 scene 9120 - donor 2243 - skull 236 + 2 (foot rounding) = 6643 ✓
rev3 scene 16276 - donor 2243                                = 14033 ✓
```

So the handoff figure is **not "simply wrong"** -- it is the correct sum for
`parts.json` at each revision. It is however not the whole scene. This tool
reports scene truth; anywhere the two disagree, the scene wins because it is
what Blender opens. The reconciliation is pinned by
`test_rev2_and_rev3_whole_scene_totals`.

## 3. No-change control (skirt)

`SKS_Garment_UnderMailSkirt_01` is claimed to hold at 362 polys across source,
rev2, and rev3. All three probes report exactly `polys=362, verts=384`. Pinned
by `test_undermailskirt_is_the_no_change_control_across_all_three_revisions`.

## 4. Round-trip: object-preserving, scene-VIOLATED

The claim was that `source/*.blend` → `staging/roundtrip/*.blend` is
topology-preserving. Probes:

- source has 19 mesh objects; roundtrip has 18.
- The 18 shared objects match `polys` and `verts` exactly across every entry.
- Roundtrip drops `SKS_Donor_BodyBelowChin_01`.

Reported honestly, this is **per-object topology preserved for every surviving
object, whole-scene topology VIOLATED because one object was removed.**

Feeding both probes to `pil_contract_verdict --scene-stats-a
source.json --scene-stats-b roundtrip.json` with
`--invariant geometry.topology_preserved --invariant
geometry.topology_preserved(SKS_Garment_Tabard_01)` returns:

- `geometry.topology_preserved` → **VIOLATED**, evidence cites
  `objects_removed_from_b: [SKS_Donor_BodyBelowChin_01]` and
  `objects_with_changed_counts: []`.
- `geometry.topology_preserved(SKS_Garment_Tabard_01)` → **SATISFIED**.

Pinned by
`test_source_to_roundtrip_topology_is_object_preserving_but_scene_violated`.

## 5. Test results

Full suite: `uv run python -m pytest -q` → **491 passed, 6 skipped** (6 skips
are pre-existing image-alpha tests that skip when the optional reference image
is absent -- unrelated to this work). All 22 tests in
`tests/test_blender_mesh.py` pass, including the six corpus-gated tests that
spawn real Blender against the four probes above.

`tests/test_contract_verdict.py` alone: **42 passed**, unmodified from the
pre-work baseline, which confirms the diff to `scripts/pil_contract_verdict.py`
only touched the geometry.\* refusal path.
