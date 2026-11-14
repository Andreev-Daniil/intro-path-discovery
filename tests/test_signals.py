from datetime import date

from intro_paths.interactions import attach_interactions
from intro_paths.models import (
    Affiliation, Coauthor, Insider, Interaction, Network, Period, Person, Target, Work,
)
from intro_paths.normalize import normalize_org
from intro_paths.paths import find_intro_paths
from intro_paths.store import load_network, load_target, save_network, save_target

TODAY = date(2026, 11, 15)


def aff(kind, name):
    return Affiliation(kind, name, normalize_org(name), Period(), "")


def person(pid, first, last, *affs, **kw):
    return Person(pid, first, last, affiliations=list(affs), **kw)


def company_target():
    return Target(
        "Acme Corp", [aff("position", "Acme Corp")], kind="company",
        insiders=[
            Insider("Jane Doe", is_director=True, last_seen=date(2025, 1, 1), source_url="https://sec/x"),
            Insider("Bob Roe", is_officer=True, title="CFO", last_seen=date(2012, 1, 1)),
        ],
    )


def test_insider_corroborated_vs_name_only():
    net = Network(Person("me"), {
        "a": person("a", "Jane", "Doe", aff("position", "Acme Corp")),
        "b": person("b", "Jane", "Doe", aff("position", "Elsewhere")),
    })
    paths = {p.connector.id: p for p in find_intro_paths(net, company_target(), today=TODAY)}
    a = paths["a"]
    assert {s.code for s in a.signals} == {"same_employer", "insider_director"}
    insider = next(s for s in a.signals if s.code == "insider_director")
    assert insider.weight == 0.9 and insider.evidence_url == "https://sec/x"
    assert paths["b"].strength == 0.45  # только имя -> ×0.5


def test_stale_officer_is_discounted():
    net = Network(Person("me"), {"c": person("c", "Bob", "Roe", aff("position", "Acme Corp"))})
    sig = next(s for s in find_intro_paths(net, company_target(), today=TODAY)[0].signals
               if s.code == "insider_officer")
    assert sig.weight == round(0.7 * 0.7, 4)


def person_target():
    work = Work("W1", "Graphs", 2024, "10.1/x", "https://openalex.org/W1",
                (Coauthor("Bob Roe", "A9", ("MIT",)),))
    return Target("Jane Doe", kind="person", works=[work])


def test_coauthor_by_name_and_confirmed_by_shared_work_id():
    unconfirmed = person("u", "Bob", "Roe", aff("position", "Somewhere"))
    by_inst = person("i", "Bob", "Roe", aff("education", "MIT"))
    by_id = person("d", "Bob", "Roe", works=[Work("W1", "Graphs", 2024)])
    net = Network(Person("me"), {"u": unconfirmed, "i": by_inst, "d": by_id})
    got = {p.connector.id: p.strength for p in find_intro_paths(net, person_target(), today=TODAY)}
    assert got == {"d": 0.5, "i": 0.5, "u": 0.25}


def test_warmth_scales_strength_only_when_interactions_exist():
    cold = person("x", "Bob", "Roe", aff("position", "Acme Corp"))
    warm = person("y", "Bob", "Roe", aff("position", "Acme Corp"),
                  interactions=[Interaction("message", date(2026, 10, 1), 5), Interaction("invite", date(2020, 1, 1))])
    net = Network(Person("me"), {"x": cold, "y": warm})
    target = Target("Acme Corp", [aff("position", "Acme Corp")])
    got = {p.connector.id: p for p in find_intro_paths(net, target, today=TODAY)}
    # в сети есть данные о переписке: контакт без неё — самый холодный (×0.5)
    assert got["x"].strength == 0.3
    assert got["y"].strength == 0.6  # тепло 1.0 -> множитель 1.0
    assert got["y"].signals[-1].code == "interaction"
    lukewarm = person("z", "Bob", "Roe", aff("position", "Acme Corp"),
                      interactions=[Interaction("message", date(2026, 10, 1), 1)])
    net2 = Network(Person("me"), {"z": lukewarm})
    assert find_intro_paths(net2, target, today=TODAY)[0].strength < 0.6


def test_attach_interactions_from_export(tmp_path):
    (tmp_path / "Messages.csv").write_text(
        "CONVERSATION ID,CONVERSATION TITLE,FROM,SENDER PROFILE URL,TO,RECIPIENT PROFILE URLS,DATE,SUBJECT,CONTENT,FOLDER\n"
        "1,,Me,https://www.linkedin.com/in/me,Bob,https://www.linkedin.com/in/bob-roe,2026-05-01 10:00:00 UTC,,hi,INBOX\n"
        "1,,Bob,https://www.linkedin.com/in/bob-roe,Me,https://www.linkedin.com/in/me,2026-05-02 10:00:00 UTC,,yo,INBOX\n",
        encoding="utf-8")
    (tmp_path / "Invitations.csv").write_text(
        "From,To,Sent At,Message,Direction,inviterProfileUrl,inviteeProfileUrl\n"
        "Me,Bob,2019-01-01,,OUTGOING,https://www.linkedin.com/in/me,https://www.linkedin.com/in/bob-roe\n",
        encoding="utf-8")
    net = Network(Person("me"), {"bob-roe": Person("bob-roe", "Bob", "Roe"), "other": Person("other", "O", "T")})
    assert attach_interactions(net, tmp_path) == 1
    kinds = sorted(i.kind for i in net.contacts["bob-roe"].interactions)
    assert kinds == ["invite", "message", "message"]
    assert not net.contacts["other"].interactions


def test_store_roundtrip_new_fields(tmp_path):
    n = Network(Person("me"), {"w": person("w", "A", "B", works=[Work("W1", "T", 2020, "d", "u", (Coauthor("C D", "A1", ("MIT",)),))],
                                          interactions=[Interaction("message", date(2024, 1, 2), 3)])})
    save_network(n, tmp_path / "n.json")
    again = load_network(tmp_path / "n.json").contacts["w"]
    assert again.works[0].coauthors[0].institutions == ("MIT",)
    assert again.interactions == [Interaction("message", date(2024, 1, 2), 3)]
    save_target(company_target(), tmp_path / "t.json")
    t = load_target(tmp_path / "t.json")
    assert t.kind == "company" and t.insiders[0].is_director and t.insiders[1].title == "CFO"


def test_old_network_json_still_loads(tmp_path):
    (tmp_path / "old.json").write_text(
        '{"owner":{"id":"me"},"contacts":[{"id":"a","first_name":"A","last_name":"B","affiliations":[]}]}',
        encoding="utf-8")
    assert load_network(tmp_path / "old.json").contacts["a"].works == []
