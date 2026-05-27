from modules import change_memory


def test_change_memory_filters_implemented_url(tmp_path):
    csv_file = tmp_path / "controle.csv"
    csv_file.write_text(
        "Data,URL,Tipo de Mudança,Elemento Alterado,Antes,Depois,Status,Observações\n"
        "10/04/2026,https://www.secretoutlet.com.br/tenis-lacoste,On-page,"
        '"Title, Description, H1, Faq",,,Implementado,Melhorias publicadas\n',
        encoding="utf-8-sig",
    )

    records = change_memory.load_records(csv_file)
    items = [
        {
            "source": "gsc",
            "action": "Criar ou otimizar guia de compra para produto + marca",
            "target": "tenis lacoste",
            "priority": 50,
            "evidence": {},
        },
        {
            "source": "gsc",
            "action": "Criar ou otimizar guia de compra para produto + marca",
            "target": "camiseta lacoste",
            "priority": 40,
            "evidence": {},
        },
    ]

    kept, suppressed = change_memory.filter_backlog_items(items, records)

    assert [item["target"] for item in kept] == ["camiseta lacoste"]
    assert suppressed[0]["target"] == "tenis lacoste"
    assert suppressed[0]["suppressed_by_change"]["status"] == "Implementado"


def test_change_memory_does_not_suppress_current_technical_sources(tmp_path):
    csv_file = tmp_path / "controle.csv"
    csv_file.write_text(
        "Data,URL,Tipo de Mudança,Elemento Alterado,Antes,Depois,Status,Observações\n"
        "10/04/2026,https://www.secretoutlet.com.br/lacoste,On-page,H1,,,Implementado,\n",
        encoding="utf-8-sig",
    )

    records = change_memory.load_records(csv_file)
    items = [
        {
            "source": "links",
            "action": "Corrigir link quebrado ou criar redirect 301",
            "target": "https://www.secretoutlet.com.br/lacoste",
            "priority": 80,
            "evidence": {},
        }
    ]

    kept, suppressed = change_memory.filter_backlog_items(items, records)

    assert kept == items
    assert suppressed == []


def test_change_memory_matches_plural_and_modifier_queries(tmp_path):
    csv_file = tmp_path / "controle.csv"
    csv_file.write_text(
        "Data,URL,Tipo de Mudança,Elemento Alterado,Antes,Depois,Status,Observações\n"
        "10/04/2026,https://www.secretoutlet.com.br/camisetas-lacoste,On-page,H1,,,Implementado,\n"
        "10/04/2026,https://www.secretoutlet.com.br/tenis-reserva,On-page,H1,,,Implementado,\n",
        encoding="utf-8-sig",
    )

    records = change_memory.load_records(csv_file)

    assert change_memory.match_record("camiseta lacoste", records)
    assert change_memory.match_record("tenis reserva masculino", records)
