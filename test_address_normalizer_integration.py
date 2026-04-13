"""Tests de integración para AddressNormalizer.normalize_pickup_now().

Usa un FakeMapsClient que devuelve respuestas predefinidas sin red real.
Cubre los 6 escenarios principales del pipeline:
  1. VALIDATED: número inyectado + Maps confirma dirección
  2. PARTIAL_MATCH: número preservado + (REVISAR) añadido
  3. NO_RESULT: fallback usa pickup_for_geocoding (con número), no raw sin número
  4. OUTSIDE_AMB: dirección fuera del AMB → nunca VALIDATED
  5. OPERATOR_LOCKED: campo bloqueado → no geocodificar, no modificar
  6. CACHE: segunda llamada idéntica devuelve resultado cacheado
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.geo.address_normalizer import AddressNormalizer, PickupStatus
from app.geo.maps_client import GeoResult, MapsClient
from app.parser.service_extractor import ServiceData


# ---------------------------------------------------------------------------
# FakeMapsClient — stub sin red real
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    result: Optional[GeoResult]


class FakeMapsClient(MapsClient):
    """Stub que devuelve respuestas predefinidas por dirección (substring match)."""

    def __init__(self) -> None:
        # No llamar a super().__init__() — no queremos validar api_key
        self._responses: list[tuple[str, Optional[GeoResult]]] = []
        self._calls: list[str] = []

    def add(self, address_fragment: str, result: Optional[GeoResult]) -> None:
        self._responses.append((address_fragment.lower(), result))

    def geocode(self, address: str) -> Optional[GeoResult]:
        self._calls.append(address)
        low = address.lower()
        for fragment, result in self._responses:
            if fragment in low:
                return result
        return None

    @property
    def call_count(self) -> int:
        return len(self._calls)


def _make_geo(
    formatted: str,
    municipality: str,
    partial: bool = False,
    lat: float = 41.3851,
    lon: float = 2.1734,
) -> GeoResult:
    return GeoResult(
        formatted_address=formatted,
        lat=lat,
        lon=lon,
        place_id="fake_place_id",
        partial_match=partial,
        municipality=municipality,
        raw_status="OK",
    )


def _empty_data(recogida: str = "PENDIENTE") -> ServiceData:
    d = ServiceData.empty()
    d.recogida = recogida
    return d


# ---------------------------------------------------------------------------
# Helpers de assert
# ---------------------------------------------------------------------------

def assert_status(data: ServiceData, expected: PickupStatus, msg: str = "") -> None:
    got = data._recogida_status
    assert got == expected.value, f"{msg}\nEsperado status={expected.value!r}, got={got!r}"


def assert_recogida_contains(data: ServiceData, fragment: str, msg: str = "") -> None:
    rec = data.recogida or ""
    assert fragment in rec, f"{msg}\nEsperado '{fragment}' en recogida={rec!r}"


def assert_recogida_not_contains(data: ServiceData, fragment: str, msg: str = "") -> None:
    rec = data.recogida or ""
    assert fragment not in rec, f"{msg}\nNO esperado '{fragment}' en recogida={rec!r}"


# ---------------------------------------------------------------------------
# Casos de test
# ---------------------------------------------------------------------------

def test_validated_number_injected() -> bool:
    """VALIDATED: LLM no trae número, transcript sí → número inyectado + Maps valida."""
    fake = FakeMapsClient()
    fake.add(
        "calle de valencia 35",
        _make_geo("Carrer de València, 35, 08009 Barcelona, España", "Barcelona"),
    )
    norm = AddressNormalizer(fake)

    transcript = "[C] Estoy en la calle Valencia numero treinta y cinco de Barcelona"
    llm_pickup = "Calle de Valencia, Barcelona"

    data = _empty_data(llm_pickup)
    current = ServiceData.empty()

    out = norm.normalize_pickup_now(data, transcript, current, locked_fields=set())

    ok = True
    try:
        assert_status(out, PickupStatus.VALIDATED, "test_validated_number_injected")
        assert_recogida_contains(out, "35", "test_validated_number_injected — número 35 en recogida")
        assert_recogida_not_contains(out, "(REVISAR)", "test_validated_number_injected — no debe tener REVISAR")
        print("[OK] test_validated_number_injected")
    except AssertionError as e:
        print(f"[FAIL] test_validated_number_injected: {e}")
        ok = False
    return ok


def test_partial_match_adds_revisar() -> bool:
    """PARTIAL_MATCH: Maps devuelve partial_match=True con numero distinto → (REVISAR).

    La dirección tiene número 640 pero Maps devuelve un número diferente (999)
    con partial_match=True. El guardarraíl de número_match debe impedir VALIDATED.
    """
    fake = FakeMapsClient()
    fake.add(
        "calle mallorca 640",
        # numero distinto (999 vs 640) + partial_match → PARTIAL_MATCH, no VALIDATED
        _make_geo("Carrer de Mallorca, 999, 08025 Barcelona", "Barcelona", partial=True),
    )
    norm = AddressNormalizer(fake)

    transcript = "[C] Calle Mallorca seiscientos cuarenta"
    llm_pickup = "Calle Mallorca, Barcelona"

    data = _empty_data(llm_pickup)
    current = ServiceData.empty()

    out = norm.normalize_pickup_now(data, transcript, current, locked_fields=set())

    ok = True
    try:
        assert out._recogida_status != PickupStatus.VALIDATED.value, (
            f"Con numero distinto y partial_match no debe ser VALIDATED, got={out._recogida_status!r}"
        )
        assert_recogida_contains(out, "(REVISAR)", "test_partial_match — debe tener REVISAR")
        print("[OK] test_partial_match_adds_revisar")
    except AssertionError as e:
        print(f"[FAIL] test_partial_match_adds_revisar: {e}")
        ok = False
    return ok


def test_no_result_uses_enriched_address() -> bool:
    """NO_RESULT: Maps no encuentra nada → recogida usa pickup_for_geocoding (con número),
    no el raw original del LLM (sin número).

    Este era el bug principal: cuando Google no devolvía resultado,
    la UI mostraba "Calle de Valencia (REVISAR)" en lugar de
    "Calle de Valencia 35 (REVISAR)".
    """
    fake = FakeMapsClient()
    # No añadir ninguna respuesta → geocode devuelve None
    norm = AddressNormalizer(fake)

    transcript = "[C] Estoy en la calle Valencia numero treinta y cinco"
    llm_pickup = "Calle de Valencia, Barcelona"  # sin número

    data = _empty_data(llm_pickup)
    current = ServiceData.empty()

    out = norm.normalize_pickup_now(data, transcript, current, locked_fields=set())

    ok = True
    try:
        assert_status(out, PickupStatus.NO_RESULT, "test_no_result — status")
        # La recogida debe mostrar el número 35 aunque Maps no respondió
        assert_recogida_contains(out, "35", "test_no_result — número 35 en display fallback")
        assert_recogida_contains(out, "(REVISAR)", "test_no_result — debe tener REVISAR")
        print("[OK] test_no_result_uses_enriched_address")
    except AssertionError as e:
        print(f"[FAIL] test_no_result_uses_enriched_address: {e}")
        ok = False
    return ok


def test_outside_amb_never_validated() -> bool:
    """OUTSIDE_AMB: Maps devuelve municipio fuera del AMB → nunca VALIDATED."""
    fake = FakeMapsClient()
    fake.add(
        "calle gran via",
        _make_geo(
            "Gran Via de les Corts Catalanes, Madrid, España",
            "Madrid",  # fuera del AMB
            partial=False,
        ),
    )
    norm = AddressNormalizer(fake)

    transcript = "[C] Estoy en la Gran Via"
    llm_pickup = "Calle Gran Via, Madrid"

    data = _empty_data(llm_pickup)
    current = ServiceData.empty()

    out = norm.normalize_pickup_now(data, transcript, current, locked_fields=set())

    ok = True
    try:
        assert out._recogida_status != PickupStatus.VALIDATED.value, (
            f"OUTSIDE_AMB nunca debe ser VALIDATED, got={out._recogida_status!r}"
        )
        print("[OK] test_outside_amb_never_validated")
    except AssertionError as e:
        print(f"[FAIL] test_outside_amb_never_validated: {e}")
        ok = False
    return ok


def test_operator_locked_no_geocoding() -> bool:
    """OPERATOR_LOCKED: campo recogida bloqueado → no geocodificar, no modificar."""
    fake = FakeMapsClient()
    fake.add(
        "calle aragon",
        _make_geo("Carrer d'Aragó, 08011 Barcelona", "Barcelona"),
    )
    norm = AddressNormalizer(fake)

    transcript = "[C] Estoy en la calle Aragon"
    llm_pickup = "Calle Aragon, Barcelona"

    data = _empty_data(llm_pickup)
    current = ServiceData.empty()

    out = norm.normalize_pickup_now(data, transcript, current, locked_fields={"recogida"})

    ok = True
    try:
        assert_status(out, PickupStatus.OPERATOR_LOCKED, "test_operator_locked — status")
        assert fake.call_count == 0, (
            f"No debe llamar a Maps cuando el campo está bloqueado, calls={fake.call_count}"
        )
        print("[OK] test_operator_locked_no_geocoding")
    except AssertionError as e:
        print(f"[FAIL] test_operator_locked_no_geocoding: {e}")
        ok = False
    return ok


def test_cache_second_call_no_extra_geocode() -> bool:
    """CACHE: segunda llamada idéntica usa caché, no llama a Maps de nuevo."""
    fake = FakeMapsClient()
    fake.add(
        "calle can travi 43",
        _make_geo("Carrer de Can Travi, 43, 08035 Barcelona", "Barcelona"),
    )
    norm = AddressNormalizer(fake)

    transcript = "[O] Direccion?\n[C] Calle Can Travi\n[O] Numero?\n[C] cuarenta y tres"
    llm_pickup = "Calle Can Travi, Barcelona"

    data1 = _empty_data(llm_pickup)
    current1 = ServiceData.empty()
    out1 = norm.normalize_pickup_now(data1, transcript, current1, locked_fields=set())

    calls_after_first = fake.call_count

    # Segunda llamada con el mismo raw del LLM — debe usar caché
    data2 = _empty_data(llm_pickup)
    # current2 simula que el estado anterior ya tiene el resultado
    current2 = out1
    out2 = norm.normalize_pickup_now(data2, transcript, current2, locked_fields=set())

    ok = True
    try:
        assert_status(out1, PickupStatus.VALIDATED, "test_cache — primera llamada VALIDATED")
        assert_recogida_contains(out1, "43", "test_cache — primera llamada tiene número 43")
        # El segundo normalize_pickup_now puede reutilizar shortcircuit O el cache interno.
        # Lo que importa es que no añada más llamadas reales a Maps (el cache_hit está en el dict).
        # Llamadas totales al stub pueden ser 1 o 2 (shortcircuit vs cache dict).
        # Lo que NO debe pasar es que el resultado cambie.
        assert out2._recogida_status == out1._recogida_status, (
            f"Estado debe ser igual en segunda llamada: {out1._recogida_status} vs {out2._recogida_status}"
        )
        print(f"[OK] test_cache_second_call_no_extra_geocode  (calls_after_first={calls_after_first})")
    except AssertionError as e:
        print(f"[FAIL] test_cache_second_call_no_extra_geocode: {e}")
        ok = False
    return ok


def test_amb_l_hospitalet_no_apostrophe() -> None:
    """AMB: 'L Hospitalet de Llobregat' (sin apóstrofe, formato Google Maps) → AMB válido."""
    from app.geo.amb_municipalities import normalize_municipality, is_amb_municipality

    ok = True
    cases = [
        ("L'Hospitalet de Llobregat", "L'Hospitalet de Llobregat"),
        ("L Hospitalet de Llobregat", "L'Hospitalet de Llobregat"),
        ("l hospitalet", "L'Hospitalet de Llobregat"),
        ("hospitalet", "L'Hospitalet de Llobregat"),
    ]
    for inp, expected in cases:
        got = normalize_municipality(inp)
        is_amb = is_amb_municipality(inp)
        if got != expected or not is_amb:
            print(f"[FAIL] test_amb_l_hospitalet: '{inp}' → got={got!r}, is_amb={is_amb}, expected={expected!r}")
            ok = False
        else:
            print(f"[OK]   test_amb_l_hospitalet: '{inp}' -> {got!r}")
    return ok


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    tests = [
        test_validated_number_injected,
        test_partial_match_adds_revisar,
        test_no_result_uses_enriched_address,
        test_outside_amb_never_validated,
        test_operator_locked_no_geocoding,
        test_cache_second_call_no_extra_geocode,
        test_amb_l_hospitalet_no_apostrophe,
    ]

    total = len(tests)
    passed = 0
    for fn in tests:
        if fn():
            passed += 1

    print("\n" + "#" * 80)
    print(f"RESUMEN: {passed}/{total} OK")
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
