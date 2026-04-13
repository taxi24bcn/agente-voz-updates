import re

from app.geo.pickup_repair import (
    merge_llm_pickup_with_transcript_number,
    extract_best_pickup_from_transcript,
)
from app.geo.pickup_preprocessor import preprocess


TEST_CASES = [
    {
        "name": "OK - numero explicito en misma frase",
        "transcript": "[C] Estoy en la calle Valencia numero treinta y cinco de Barcelona",
        "llm_pickup": "Calle de Valencia, Barcelona",
        "expected_merge": "Calle de Valencia 35, Barcelona",
        "must_have_number": "35",
        "must_not_have_number": None,
    },
    {
        "name": "OK - numero en digitos misma frase",
        "transcript": "[C] Estoy en la calle Valencia 35",
        "llm_pickup": "Calle de Valencia, Barcelona",
        "expected_merge": "Calle de Valencia 35, Barcelona",
        "must_have_number": "35",
        "must_not_have_number": None,
    },
    {
        "name": "OK - numero separado en otra linea",
        "transcript": "[O] Direccion?\n[C] Calle Can Travi\n[O] Numero?\n[C] cuarenta y tres",
        "llm_pickup": "Calle Can Travi, Barcelona",
        "expected_merge": "Calle Can Travi 43, Barcelona",
        "must_have_number": "43",
        "must_not_have_number": None,
    },
    {
        "name": "OK - numero dicho como articulo + centenas",
        "transcript": "[C] Estoy en la calle Mallorca el seiscientos cuarenta",
        "llm_pickup": "Calle Mallorca, Barcelona",
        "expected_merge": "Calle Mallorca 640, Barcelona",
        "must_have_number": "640",
        "must_not_have_number": None,
    },
    {
        "name": "OK - LLM ya trae numero",
        "transcript": "[C] Estoy en la calle Valencia numero treinta y cinco",
        "llm_pickup": "Calle de Valencia 35, Barcelona",
        "expected_merge": "Calle de Valencia 35, Barcelona",
        "must_have_number": "35",
        "must_not_have_number": None,
    },
    {
        "name": "NO - no debe coger numero de personas",
        "transcript": "[C] Estoy en la calle Valencia, somos cuatro personas",
        "llm_pickup": "Calle de Valencia, Barcelona",
        "expected_merge": "Calle de Valencia, Barcelona",
        "must_have_number": None,
        "must_not_have_number": "4",
    },
    {
        "name": "NO - no debe coger piso/puerta",
        "transcript": "[C] Estoy en la calle Aragon, piso dos puerta uno",
        "llm_pickup": "Calle Aragon, Barcelona",
        "expected_merge": "Calle Aragon, Barcelona",
        "must_have_number": None,
        "must_not_have_number": "2",
    },
    {
        "name": "NO - no debe coger hora",
        "transcript": "[C] Estoy en la calle Mallorca, a las cinco",
        "llm_pickup": "Calle Mallorca, Barcelona",
        "expected_merge": "Calle Mallorca, Barcelona",
        "must_have_number": None,
        "must_not_have_number": "5",
    },
    {
        "name": "NO - no debe coger telefono",
        "transcript": "[C] Estoy en la calle Valencia y mi telefono es 622878040",
        "llm_pickup": "Calle de Valencia, Barcelona",
        "expected_merge": "Calle de Valencia, Barcelona",
        "must_have_number": None,
        "must_not_have_number": "622878040",
    },
    {
        "name": "OK - cafe -> calle si es direccion postal",
        "transcript": "[C] Estoy en cafe Mallorca numero cuatrocientos tres",
        "llm_pickup": "Cafe Mallorca, Barcelona",
        "expected_merge": "Cafe Mallorca 403, Barcelona",
        "must_have_number": "403",
        "must_not_have_number": None,
    },
]


def has_house_number(text: str) -> bool:
    return bool(re.search(r"\b\d{1,4}[A-Za-z]?\b", text or ""))


def run_case(case: dict) -> bool:
    merged = merge_llm_pickup_with_transcript_number(case["transcript"], case["llm_pickup"])
    repair = extract_best_pickup_from_transcript(case["transcript"], case["llm_pickup"])
    prep = preprocess(repair.address_for_geocoding)

    ok = True

    if merged != case["expected_merge"]:
        ok = False

    if case["must_have_number"] is not None:
        if case["must_have_number"] not in merged:
            ok = False
        if case["must_have_number"] not in repair.address_for_geocoding:
            ok = False
        if case["must_have_number"] not in prep.cleaned:
            ok = False

    if case["must_not_have_number"] is not None:
        forbidden = case["must_not_have_number"]
        if forbidden in merged:
            ok = False
        if forbidden in repair.address_for_geocoding:
            ok = False
        if forbidden in prep.cleaned:
            ok = False

    print("=" * 80)
    print(case["name"])
    print(f"TRANSCRIPT:              {case['transcript']!r}")
    print(f"LLM_PICKUP:              {case['llm_pickup']!r}")
    print(f"MERGED:                  {merged!r}")
    print(f"EXPECTED_MERGE:          {case['expected_merge']!r}")
    print(f"REPAIR.address_for_geo:  {repair.address_for_geocoding!r}")
    print(f"REPAIR.correction:       {repair.correction_detected!r}")
    print(f"PREPROCESS.cleaned:      {prep.cleaned!r}")
    print(f"PREPROCESS.query_type:   {prep.query_type.value!r}")
    print(f"HAS_HOUSE_NUMBER:        {has_house_number(prep.cleaned)}")
    print(f"RESULT:                  {'OK' if ok else 'FAIL'}")
    return ok


def main():
    total = len(TEST_CASES)
    passed = 0

    for case in TEST_CASES:
        if run_case(case):
            passed += 1

    print("\n" + "#" * 80)
    print(f"RESUMEN: {passed}/{total} OK")
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
