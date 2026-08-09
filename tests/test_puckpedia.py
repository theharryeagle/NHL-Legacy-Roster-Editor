from nhl_legacy_editor.puckpedia import parse_puckpedia_depth_chart


def test_depth_chart_parser_reads_full_names_and_roster_sections() -> None:
    html = """
    <div>Lines as of 2026-27 | Contracts as of 2026-27</div>
    <div>Last Updated: Jul 27, 10:15 AM ET</div>
    <table>
      <tbody><tr><th>Forwards</th></tr></tbody>
      <tbody><tr><td><a href="https://puckpedia.com/player/i/1207">
        Crosby, <span class="max-sm:hidden">Sidney</span><span class="sm:hidden">S.</span>
      </a></td></tr></tbody>
      <tbody><tr><th>Minor-League Skaters</th></tr></tbody>
      <tbody><tr><td><a href="https://puckpedia.com/player/i/9999">
        Prospect, <span class="max-sm:hidden">Pat</span><span class="sm:hidden">P.</span>
      </a></td></tr></tbody>
    </table>
    """

    chart = parse_puckpedia_depth_chart(html, "PIT")

    assert chart.lines_season == "2026-27"
    assert chart.contracts_season == "2026-27"
    assert chart.last_updated == "Jul 27, 10:15 AM ET"
    assert [(row.full_name, row.status) for row in chart.players] == [
        ("Sidney Crosby", "nhl"),
        ("Pat Prospect", "minors"),
    ]
