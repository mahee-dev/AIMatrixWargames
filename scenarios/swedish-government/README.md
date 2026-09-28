# AMAW-scenario: Regeringsbildningen 2026 — 7.1.0

Ett Matrix Game för autonoma AI-agenter om regeringsbildningen efter
riksdagsvalet 2026. Det är **inte en prognos** över vilken regering som
faktiskt kommer att bildas.

```bash
uv run amaw run swedish-government
```

## Verklighet och experiment

Scenariot är fryst vid **informationsläget den 25 september 2026 kl. 12.00**.

Paketet innehåller:

1. **historiska fakta** fram till frysen: valresultatet, statsministerns
   avgång, talmannens sonderingsuppdrag och budgetklockan;
2. **offentliga partipositioner** fram till frysen, med daterade källor;
3. **fiktiva privata incitament** i `scenario.yaml`, som inte beskriver
   partiernas verkliga interna överväganden.

Allt som Actors gör efter spelets start är **kontrafaktisk spelhistoria**. Om
en Actor förhandlar fram S+M betyder det inte att sådana förhandlingar ägt rum.
Det som händer i verkligheten efter frysen ingår inte i scenariot och ändrar
inte en körning i efterhand.

För jämförbara körningar ska Actors inte ha tillgång till extern sökning. Då
kan de inte hämta in vad som hände efter frysen.

Experimentfrågan:

> Givet den fastställda mandatfördelningen 2026 och allt som var offentligt
> känt den 25 september: vilka regeringspaket förhandlar autonoma agenter fram?

## Paketet

```text
scenario.yaml
README.md
AUTHOR_NOTES.md                  # author/test-harness only; not an Actor Resource
CHANGELOG.md
resources/
  valresultat_2026.csv           # det fastställda valresultatet, enda mandatkällan
  regeringsregler.md             # procedur, avtal, röstlägen, budgetväg
  regeringsbildning-status.md    # vad som hänt institutionellt, tidslinje, slutpunkt
  offentliga-positioner.md       # partipositioner före och efter valet
  valintegritet-2026.md          # fastställt och påstått om valet, separat märkt
  forhandlingsfragor.md          # dokumenterade sakpolitiska konflikter
```

Varje Resource svarar på en fråga. Mandat hämtas bara från
`valresultat_2026.csv`.

## Tre lager

- **`rules`** i `scenario.yaml` — sex korta invarianter som Umpiren väger in vid
  varje adjudikation: 175-nej-testet, röstlägena, egna åtaganden,
  flerpartsavtal, att anklagelser inte ändrar mandat, och att stora steg kräver
  partistyrelsens eller riksdagsgruppens godkännande. Det godkännandet är
  scenariots enda sannolikhetsfråga om ett partis hållning.
- **Resources** — information och förklaring. `regeringsregler.md` förklarar
  reglerna och ger exempel.
- **Effects** — det auktoritativa aktuella läget. Starttillståndet innehåller
  bara det fastställda: valresultatet, övergångsregeringen,
  sonderingsuppdraget, och att inget förslag ännu har prövats.

Konflikten mellan C och V, Borlängeärendet och tänkbara regeringar är **inte**
Effects. De finns i Resources och blir spelhistoria först genom Actorernas
handlingar.

## Slutpunkt

Fyra turns om 48 timmar täcker åtta dagar. Spelet slutar med ett genomförbart
regeringspaket som talmannen skulle kunna lägga fram, eller med ett
dokumenterat misslyckande. Paketet prövas hypotetiskt mot 175-nej-regeln.

## Spelvillkor

Från 7.1.0 deklarerar `game` begränsad diplomati med tre rundor per turn
(`diplomacy_mode: bounded_rounds`). En flagga eller ett `AMAW_*`-värde går
fortfarande före.

## Oförändrat från version 6

Actorerna, turordningen `S → M → SD → C → V → KD → MP → L`, fyra turns om
48 timmar, `max_cons_per_actor: 1` och de privata fiktiva incitamenten är
desamma. Version 6 (med förfinal mandatfil och fiktivt procedurläge) finns i
git-taggen `scenario/svensk-regeringsbildning-2026/6`. Ändringarna står i
`CHANGELOG.md`.

## Validering före dyr körning

1. Kör projektets testsuite.
2. Kontrollera att endast manifestdeklarerade Resources är Actor-sökbara.
3. Gör en billig provkörning före dyrare modeller.

## Känd begränsning

När relevanta röstpositioner är kända är 175-nej-testet deterministiskt.
Servern utför ingen sådan härledning; regeln i `rules` säger åt Umpiren att
inte göra summeringen probabilistisk. En deterministisk validering av slutliga
paket i utvärderingen är inte byggd.
