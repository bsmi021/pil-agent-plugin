"""RED tests for WP3 (contract-driven verdicts) and WP4 (multi-pair aggregation).

The two failures these pin are the ones the phase-2 scope names as
disqualifying:

  * **Approximating an UNMEASURABLE predicate.** Edge density looks like a
    polygon-count proxy and is not one. Every refuse-list predicate must come
    back UNMEASURABLE with *no numbers attached at all* -- a plausible number is
    how an eager tool does damage.
  * **A SATISFIED invariant presented as a guarantee.** "Invariant satisfied"
    and "no change detected" are different claims, so every negative finding has
    to carry the detection limit of the metric that decided it, and when no
    calibration bundle is loaded that limit must say so rather than being a
    number someone invented.

WP4 adds a third: a single diverging pair must fail the item outright, never be
averaged away by healthy ones.
"""

import json

import pytest
from PIL import Image

from conftest import preview_render

CYAN = (30, 200, 200)
RED = (220, 30, 30)

UNCALIBRATED = "uncalibrated: detection limit not available"


def write_contract(tmp_path, expect=(), invariant=(), name="contract.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps({"expect_change": list(expect), "invariant": list(invariant)}),
        encoding="utf-8",
    )
    return path


def write_pairs(tmp_path, pairs, name="pairs.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps([{"a": str(a), "b": str(b)} for a, b in pairs]), encoding="utf-8"
    )
    return path


def item_for(result, predicate, pair=0):
    """The single item for `predicate` in one pair's block."""
    matches = [i for i in result["pairs"][pair]["items"] if i["predicate"] == predicate]
    assert len(matches) == 1, f"expected exactly one {predicate!r} item, got {matches}"
    return matches[0]


def numbers_in(value):
    """Every numeric leaf under `value`, booleans excluded.

    Used to assert that a refused predicate ships no measurement at all: a
    number in a refusal is an approximation wearing a disclaimer.
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numbers_in(v)]
    if isinstance(value, list):
        return [n for v in value for n in numbers_in(v)]
    return []


@pytest.fixture
def sword_pair(tmp_img):
    """The canonical asset pair: same sword, gem recoloured cyan -> red."""
    return (
        tmp_img(preview_render(accent=CYAN), "a.png"),
        tmp_img(preview_render(accent=RED), "b.png"),
    )


class TestRefuseList:
    """`UNMEASURABLE` must never silently degrade into an approximation."""

    @pytest.mark.parametrize(
        "predicate,fragment",
        [
            ("geometry.poly_count.decrease", "scene mesh statistics"),
            ("geometry.topology_preserved", "scene mesh statistics"),
            ("style.more_stylised", "not reducible to pixel statistics"),
            ("style.painterly", "not reducible to pixel statistics"),
            ("identity.same_character", "not reducible to pixel statistics"),
        ],
    )
    def test_refused_predicates_are_unmeasurable(
        self, tool, tmp_path, sword_pair, predicate, fragment
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=[predicate])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, predicate)

        # Assert
        assert item["verdict"] == "UNMEASURABLE"
        assert fragment in item["reason"]

    @pytest.mark.parametrize(
        "predicate",
        [
            "geometry.poly_count.decrease",
            "style.more_stylised",
            "identity.same_character",
            "colour.vibes",
        ],
    )
    def test_a_refusal_carries_no_numbers(self, tool, tmp_path, sword_pair, predicate):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, invariant=[predicate])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, predicate)

        # Assert: no evidence, no detection limit, no numeric leaf anywhere.
        assert item["evidence"] == {}
        assert item["detection_limit"] is None
        assert numbers_in(item) == [], f"{predicate} leaked a measurement"

    def test_an_unknown_predicate_is_unmeasurable(self, tool, tmp_path, sword_pair):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["palette.vibier"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "palette.vibier")

        # Assert
        assert item["verdict"] == "UNMEASURABLE"
        assert "unknown predicate" in item["reason"]

    def test_a_refused_family_is_refused_even_parameterised(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange: a parameterised spelling must not slip past the family rule
        # into "unknown predicate", which reads as a vocabulary gap rather than
        # a refusal.
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["geometry.poly_count(1200)"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "geometry.poly_count(1200)")

        # Assert
        assert item["verdict"] == "UNMEASURABLE"
        assert "scene mesh statistics" in item["reason"]

    def test_the_refuse_list_is_advertised_in_parameters(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, invariant=["exact.unchanged"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        refused = result["parameters"]["predicates"]["refused"]

        # Assert: a calling agent can discover what will never be answered
        # without having to try it.
        assert set(refused) == {"geometry.*", "identity.same_character", "style.*"}


class TestPaletteDirection:
    def test_a_cyan_to_red_recolour_is_warmer(self, tool, tmp_path, sword_pair):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["palette.warmer"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        item = item_for(result, "palette.warmer")

        # Assert
        assert item["verdict"] == "SATISFIED"
        assert item["evidence"]["warm_family_delta"] > 0
        assert item["evidence"]["cool_family_delta"] < 0

    def test_the_same_recolour_does_not_satisfy_cooler(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange: an expect_change item that did not happen is VIOLATED -- the
        # caller asked for a cooling and did not get one.
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["palette.cooler"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        item = item_for(result, "palette.cooler")

        # Assert
        assert item["verdict"] == "VIOLATED"
        assert "expected change not observed" in item["reason"]
        # ...and it rests on "no cooling detected", so it is bounded.
        assert item["detection_limit"] == UNCALIBRATED

    def test_the_directional_margin_is_a_shipped_default_and_echoed(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["palette.warmer"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )

        # Assert: the number that decided the verdict is readable from the JSON
        # alone, together with which families count as warm and cool.
        thresholds = result["parameters"]["thresholds"]
        assert thresholds["hue_family_mass_shift"]["threshold"] == 0.02
        assert result["parameters"]["hue_families"]["warm"] == [
            "red",
            "orange",
            "yellow",
        ]
        assert result["parameters"]["hue_families"]["cool"] == [
            "cyan",
            "blue",
            "purple",
        ]

    def test_scheme_preserved_fails_on_a_recolour_and_holds_on_a_copy(
        self, tool, tmp_path, tmp_img, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        same = tmp_img(preview_render(accent=CYAN), "same.png")
        contract = write_contract(tmp_path, invariant=["palette.scheme_preserved"])

        # Act
        recoloured, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        unchanged, _ = tool(
            "pil_contract_verdict.py", a, same, "--contract", contract, "--foreground"
        )

        # Assert: the cited fields are the ones the scope names.
        broken = item_for(recoloured, "palette.scheme_preserved")
        assert broken["verdict"] == "VIOLATED"
        assert broken["evidence"]["accent_hue_shift_detected"] is True
        assert "cyan" in broken["evidence"]["hue_families_lost"]

        held = item_for(unchanged, "palette.scheme_preserved")
        assert held["verdict"] == "SATISFIED"
        assert held["evidence"]["hue_families_lost"] == []

    def test_hue_present_reads_the_supported_presence_floor_on_image_b(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(
            tmp_path,
            expect=["palette.hue_present(red)"],
            invariant=["palette.hue_present(green)"],
        )

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )

        # Assert
        present = item_for(result, "palette.hue_present(red)")
        assert present["verdict"] == "SATISFIED"
        assert present["evidence"]["pixels"] >= 10

        absent = item_for(result, "palette.hue_present(green)")
        assert absent["verdict"] == "VIOLATED"
        assert absent["evidence"]["pixels"] == 0

    def test_an_unknown_hue_family_is_unmeasurable(self, tool, tmp_path, sword_pair):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["palette.hue_present(chartreuse)"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "palette.hue_present(chartreuse)")

        # Assert
        assert item["verdict"] == "UNMEASURABLE"
        assert "unknown hue family" in item["reason"]


class TestLayout:
    def test_composition_preserved_on_identical_images_carries_its_limit(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(), "b.png")
        contract = write_contract(tmp_path, invariant=["layout.composition_preserved"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "layout.composition_preserved")

        # Assert: SATISFIED is not a guarantee, and the payload says what it
        # actually rules out. scripts/detection_limits.json ships (distilled
        # from the WP2 bundle, structural_similarity derived from
        # structural_dissimilarity), so the limit is a real calibrated bound.
        assert item["verdict"] == "SATISFIED"
        assert item["evidence"]["structural_similarity"] == 1.0
        assert item["detection_limit"]
        assert "uncalibrated" not in item["detection_limit"]
        assert "structural_similarity" in item["detection_limit"]

    def test_composition_preserved_fails_on_a_mirrored_object(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "mirrored.png")
        contract = write_contract(tmp_path, invariant=["layout.composition_preserved"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        item = item_for(result, "layout.composition_preserved")

        # Assert: a broken invariant is not a negative finding, so it carries
        # no detection limit -- the measurement itself is the evidence.
        assert item["verdict"] == "VIOLATED"
        assert item["detection_limit"] is None

    def test_region_changed_hits_and_misses_the_declared_box(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange: the gem sits at roughly x 0.11-0.14, y 0.83-0.87 of the frame.
        a, b = sword_pair
        contract = write_contract(
            tmp_path,
            expect=[
                "layout.region_changed([0.0, 0.7, 0.4, 1.0])",
                "layout.region_changed([0.6, 0.0, 1.0, 0.4])",
            ],
        )

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)

        # Assert
        hit = item_for(result, "layout.region_changed([0.0, 0.7, 0.4, 1.0])")
        assert hit["verdict"] == "SATISFIED"
        assert hit["evidence"]["changed_region_bbox_fractional"] is not None

        miss = item_for(result, "layout.region_changed([0.6, 0.0, 1.0, 0.4])")
        assert miss["verdict"] == "VIOLATED"

    def test_a_malformed_region_is_unmeasurable_not_a_crash(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["layout.region_changed([0.1, 0.2])"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "layout.region_changed([0.1, 0.2])")

        # Assert
        assert item["verdict"] == "UNMEASURABLE"
        assert "malformed region argument" in item["reason"]


class TestSilhouette:
    """Open question 1 resolved by restriction: clean masks only, never a guess."""

    def test_an_offset_copy_preserves_the_silhouette(self, tool, tmp_path, tmp_img):
        # Arrange: bbox registration must make the comparison translation
        # independent, exactly as it does for the structural grid.
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(offset=(30, 10)), "offset.png")
        contract = write_contract(
            tmp_path, invariant=["identity.silhouette_preserved"]
        )

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        item = item_for(result, "identity.silhouette_preserved")

        # Assert
        assert item["verdict"] == "SATISFIED"
        assert item["evidence"]["silhouette_iou"] == pytest.approx(1.0, abs=1e-6)
        assert item["detection_limit"] == UNCALIBRATED

    def test_a_mirrored_blade_breaks_the_silhouette(self, tool, tmp_path, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "mirrored.png")
        contract = write_contract(
            tmp_path, invariant=["identity.silhouette_preserved"]
        )

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        item = item_for(result, "identity.silhouette_preserved")

        # Assert: same bounding box, opposite diagonal -- the outline is what
        # changed, and IoU is what sees it.
        assert item["verdict"] == "VIOLATED"
        assert item["evidence"]["silhouette_iou"] < 0.85

    def test_a_uniform_image_is_unmeasurable(self, tool, tmp_path, tmp_img):
        # Arrange: no foreground separates from the background at all.
        a = tmp_img(Image.new("RGB", (400, 300), (40, 40, 44)), "flat.png")
        b = tmp_img(preview_render(), "sword.png")
        contract = write_contract(
            tmp_path, invariant=["identity.silhouette_preserved"]
        )

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "identity.silhouette_preserved")

        # Assert: refuse rather than score an empty mask.
        assert item["verdict"] == "UNMEASURABLE"
        assert "empty" in item["reason"]
        assert item["evidence"] == {}


class TestExactUnchanged:
    def test_byte_identical_images_are_exactly_unchanged(self, tool, tmp_path, tmp_img):
        # Arrange
        original = preview_render()
        a = tmp_img(original, "a.png")
        b = tmp_img(original, "b.png")
        contract = write_contract(tmp_path, invariant=["exact.unchanged"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "exact.unchanged")

        # Assert. scripts/detection_limits.json ships (generated from the WP2
        # calibration bundle), so the default run is calibrated: the limit is a
        # real bound, not the uncalibrated placeholder.
        assert item["verdict"] == "SATISFIED"
        assert item["evidence"]["dhash_distance"] == 0
        assert item["evidence"]["changed_area_fraction"] == 0.0
        assert item["detection_limit"]
        assert "uncalibrated" not in item["detection_limit"]

    def test_a_moved_object_is_not_exactly_unchanged(self, tool, tmp_path, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "mirrored.png")
        contract = write_contract(tmp_path, invariant=["exact.unchanged"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, "exact.unchanged")

        # Assert: a broken invariant is the measurement itself, so no detection
        # limit is attached -- there is no null result to bound.
        assert item["verdict"] == "VIOLATED"
        assert item["evidence"]["changed_area_fraction"] > 0.0
        assert item["detection_limit"] is None


class TestDetailCaveat:
    """detail.* is legitimately useful in 2D and legitimately dangerous as
    geometry, so the caveat is mandatory rather than conditional."""

    @pytest.mark.parametrize("predicate", ["detail.increased", "detail.decreased"])
    def test_every_detail_item_carries_the_2d_proxy_caveat(
        self, tool, tmp_path, sword_pair, predicate
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=[predicate])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        item = item_for(result, predicate)

        # Assert: whatever the verdict, the caveat travels with it.
        assert item["caveats"], f"{predicate} shipped without its caveat"
        caveat = " ".join(item["caveats"])
        assert "NOT geometry" in caveat
        assert "polygon count" in caveat

    def test_detail_cites_both_the_entropy_and_the_edge_signal(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, expect=["detail.increased"])

        # Act
        result, _ = tool("pil_contract_verdict.py", a, b, "--contract", contract)
        evidence = item_for(result, "detail.increased")["evidence"]

        # Assert: both signals must move together, so both are cited.
        assert "entropy_delta" in evidence
        assert "edge_mean_delta" in evidence


class TestDetectionLimits:
    def test_every_satisfied_invariant_carries_a_detection_limit(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange: a broad invariant-only contract over an unchanged pair, so
        # the measurable items all come back SATISFIED.
        original = preview_render()
        a = tmp_img(original, "a.png")
        b = tmp_img(original, "b.png")
        contract = write_contract(
            tmp_path,
            invariant=[
                "exact.unchanged",
                "palette.scheme_preserved",
                "layout.composition_preserved",
                "identity.silhouette_preserved",
                "geometry.poly_count",
            ],
        )

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        items = result["pairs"][0]["items"]

        # Assert: the WP3 gate. No SATISFIED invariant may read as a guarantee.
        satisfied = [i for i in items if i["verdict"] == "SATISFIED"]
        assert len(satisfied) >= 4
        for item in satisfied:
            assert item["role"] == "invariant"
            assert item["detection_limit"], (
                f"{item['predicate']} SATISFIED with no detection limit"
            )

    def test_without_calibration_the_limit_says_uncalibrated_not_a_number(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange: an explicitly empty bundle stands in for "no calibration for
        # this metric". (The default-path fallback branch itself is no longer
        # reachable on a stock checkout, because scripts/detection_limits.json
        # now ships -- this pins the uncalibrated messaging hermetically.)
        original = preview_render()
        a = tmp_img(original, "a.png")
        b = tmp_img(original, "b.png")
        contract = write_contract(tmp_path, invariant=["exact.unchanged"])
        empty_bundle = tmp_path / "empty.json"
        empty_bundle.write_text("{}", encoding="utf-8")

        # Act
        result, _ = tool(
            "pil_contract_verdict.py",
            a,
            b,
            "--contract",
            contract,
            "--thresholds",
            empty_bundle,
        )

        # Assert: never invent a detection limit.
        assert item_for(result, "exact.unchanged")["detection_limit"] == UNCALIBRATED

    def test_a_thresholds_bundle_supplies_both_limits_and_thresholds(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange: the shape the integrator will generate from the calibration
        # bundle -- metric -> {threshold, detection_limits}.
        original = preview_render()
        a = tmp_img(original, "a.png")
        b = tmp_img(original, "b.png")
        bundle = tmp_path / "limits.json"
        bundle.write_text(
            json.dumps(
                {
                    "structural_similarity": {
                        "threshold": 0.91,
                        "detection_limits": {
                            "translation": "1.5% of frame width",
                            "blur": "sigma 0.8",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        contract = write_contract(tmp_path, invariant=["layout.composition_preserved"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py",
            a,
            b,
            "--contract",
            contract,
            "--thresholds",
            bundle,
        )
        item = item_for(result, "layout.composition_preserved")

        # Assert
        assert result["parameters"]["thresholds_source"] == str(bundle)
        assert (
            result["parameters"]["thresholds"]["structural_similarity"]["threshold"]
            == 0.91
        )
        assert item["evidence"]["structural_similarity_threshold"] == 0.91
        # Sorted and joined, so the string is byte-stable across runs.
        assert item["detection_limit"] == (
            "structural_similarity / blur: sigma 0.8; "
            "structural_similarity / translation: 1.5% of frame width"
        )

    def test_a_bundle_threshold_actually_decides_the_verdict(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange: the mirrored blade fails the shipped 0.85 floor. A bundle
        # floor of 0.0 is trivially cleared, so a flip here can only come from
        # the loader honouring the file.
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "mirrored.png")
        bundle = tmp_path / "loose.json"
        bundle.write_text(
            json.dumps({"structural_similarity": {"threshold": 0.0}}), encoding="utf-8"
        )
        contract = write_contract(tmp_path, invariant=["layout.composition_preserved"])

        # Act
        shipped, _ = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        loosened, _ = tool(
            "pil_contract_verdict.py",
            a,
            b,
            "--contract",
            contract,
            "--foreground",
            "--thresholds",
            bundle,
        )

        # Assert
        assert item_for(shipped, "layout.composition_preserved")["verdict"] == "VIOLATED"
        assert (
            item_for(loosened, "layout.composition_preserved")["verdict"] == "SATISFIED"
        )


class TestContractInput:
    def test_cli_flags_extend_the_contract_file(self, tool, tmp_path, sword_pair):
        # Arrange
        a, b = sword_pair
        contract = write_contract(tmp_path, invariant=["exact.unchanged"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py",
            a,
            b,
            "--contract",
            contract,
            "--expect",
            "palette.warmer",
            "--invariant",
            "layout.composition_preserved",
        )

        # Assert
        assert result["contract"]["expect_change"] == ["palette.warmer"]
        assert result["contract"]["invariant"] == [
            "exact.unchanged",
            "layout.composition_preserved",
        ]
        assert len(result["pairs"][0]["items"]) == 3

    def test_a_bare_flag_contract_needs_no_file(self, tool, sword_pair):
        # Arrange / Act
        a, b = sword_pair
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--expect", "exact.unchanged"
        )

        # Assert
        assert result["contract"]["expect_change"] == ["exact.unchanged"]
        assert item_for(result, "exact.unchanged")["role"] == "expected"

    def test_a_single_pair_emits_no_aggregate_block(self, tool, sword_pair):
        # Arrange / Act
        a, b = sword_pair
        result, _ = tool(
            "pil_contract_verdict.py", a, b, "--invariant", "exact.unchanged"
        )

        # Assert: aggregation is a WP4 concern and must not appear where there
        # is nothing to aggregate.
        assert result["aggregate"] is None
        assert len(result["pairs"]) == 1


class TestAggregation:
    """WP4's gate: a single diverging pair cannot be averaged away."""

    @pytest.fixture
    def three_views(self, tmp_path, tmp_img):
        original = preview_render()
        a = tmp_img(original, "a.png")
        pairs = [
            (a, tmp_img(original, "b_same.png")),
            (a, tmp_img(preview_render(offset=(30, 10)), "b_offset.png")),
            (a, tmp_img(preview_render(mirrored=True), "b_mirrored.png")),
        ]
        return write_pairs(tmp_path, pairs)

    def test_one_bad_pair_among_three_fails_the_item(
        self, tool, tmp_path, three_views
    ):
        # Arrange
        contract = write_contract(tmp_path, invariant=["layout.composition_preserved"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py",
            "--pairs",
            three_views,
            "--contract",
            contract,
            "--foreground",
        )
        row = result["aggregate"][0]

        # Assert: worst-case, with the per-pair detail retained so the reader
        # can see which view broke.
        assert row["predicate"] == "layout.composition_preserved"
        assert row["verdict"] == "VIOLATED"
        assert row["pairs_violated"] == 1
        assert [p["verdict"] for p in row["pair_verdicts"]] == [
            "SATISFIED",
            "SATISFIED",
            "VIOLATED",
        ]

    def test_all_healthy_pairs_aggregate_to_satisfied(self, tool, tmp_path, tmp_img):
        # Arrange
        original = preview_render()
        a = tmp_img(original, "a.png")
        manifest = write_pairs(
            tmp_path,
            [
                (a, tmp_img(original, "b1.png")),
                (a, tmp_img(original, "b2.png")),
            ],
        )
        contract = write_contract(tmp_path, invariant=["exact.unchanged"])

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", "--pairs", manifest, "--contract", contract
        )
        row = result["aggregate"][0]

        # Assert
        assert row["verdict"] == "SATISFIED"
        assert row["pairs_violated"] == 0
        assert len(result["pairs"]) == 2

    def test_an_unmeasurable_pair_outranks_satisfied_but_not_violated(
        self, tool, tmp_path, tmp_img
    ):
        # Arrange: one clean pair, one whose foreground mask is empty.
        original = preview_render()
        a = tmp_img(original, "a.png")
        flat = tmp_img(Image.new("RGB", (400, 300), (40, 40, 44)), "flat.png")
        manifest = write_pairs(tmp_path, [(a, tmp_img(original, "b.png")), (a, flat)])
        contract = write_contract(
            tmp_path, invariant=["identity.silhouette_preserved"]
        )

        # Act
        result, _ = tool(
            "pil_contract_verdict.py", "--pairs", manifest, "--contract", contract
        )
        row = result["aggregate"][0]

        # Assert
        assert [p["verdict"] for p in row["pair_verdicts"]] == [
            "SATISFIED",
            "UNMEASURABLE",
        ]
        assert row["verdict"] == "UNMEASURABLE"
        assert row["pairs_unmeasurable"] == 1


class TestDeterminism:
    def test_repeated_single_pair_runs_are_byte_identical(
        self, tool, tmp_path, sword_pair
    ):
        # Arrange
        a, b = sword_pair
        contract = write_contract(
            tmp_path,
            expect=["palette.warmer", "geometry.poly_count.decrease"],
            invariant=["layout.composition_preserved", "identity.silhouette_preserved"],
        )

        # Act
        _, raw1 = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )
        _, raw2 = tool(
            "pil_contract_verdict.py", a, b, "--contract", contract, "--foreground"
        )

        # Assert
        assert raw1 == raw2

    def test_repeated_multi_pair_runs_are_byte_identical(
        self, tool, tmp_path, three_views_manifest
    ):
        # Arrange
        contract = write_contract(
            tmp_path,
            invariant=["exact.unchanged", "layout.composition_preserved"],
        )

        # Act
        _, raw1 = tool(
            "pil_contract_verdict.py",
            "--pairs",
            three_views_manifest,
            "--contract",
            contract,
        )
        _, raw2 = tool(
            "pil_contract_verdict.py",
            "--pairs",
            three_views_manifest,
            "--contract",
            contract,
        )

        # Assert
        assert raw1 == raw2


@pytest.fixture
def three_views_manifest(tmp_path, tmp_img):
    original = preview_render()
    a = tmp_img(original, "a.png")
    return write_pairs(
        tmp_path,
        [
            (a, tmp_img(original, "b_same.png")),
            (a, tmp_img(preview_render(offset=(30, 10)), "b_offset.png")),
            (a, tmp_img(preview_render(mirrored=True), "b_mirrored.png")),
        ],
    )
