# AMAW-scenario: Regeringsbildningen 2026

Det här scenariot är byggt för att köras **efter riksdagsvalet den 13 september 2026** med det
preliminära eller slutliga mandatresultatet som utgångspunkt.

## Före första körningen

1. Öppna `scenario.yaml`.
2. Byt ut raden i `starting_effects` som innehåller `FYLL I DET PRELIMINÄRA RESULTATET` mot de faktiska
   mandaten för varje parti.
3. Uppdatera `resources/valresultat_2026.csv` med samma mandat.
4. Om något av de åtta nuvarande riksdagspartierna **inte kommer in i riksdagen**, ta bort det från
   `actors` och `game.turn_order` innan körningen. AMAW behandlar annars även ett parti med noll mandat
   som en full spelaktör.
5. Kontrollera att mandat för alla partier som kommit in i riksdagen summerar till 349.

## Varför bara tre turns?

Åtta AI-aktörer är dyrt och långsamt jämfört med Northern Strait. Tre turns ger ändå 24 primära
Arguments och mycket diplomati. `max_cons_per_actor` är satt till 1 för att begränsa explosionsartad
interaktion.

En möjlig rytm är:

- **Turn 1:** Positionsmarkering, sonderingar och första erbjudanden.
- **Turn 2:** Koalitionsförhandlingar, röda linjer, sakpolitiska och ministeriella byten.
- **Turn 3:** Slutliga toleransbesked, statsministerkandidat och försök att låsa en regeringslösning.

## Vad som är verkligt och vad som är konstruerat

De offentliga regeringspositionerna är sammanfattade från offentliga uppgifter tillgängliga
12 september 2026. De privata briefarna är däremot medvetet **fiktiva scenarioantaganden** för att ge
AI-agenterna strategiska spänningar. De ska inte läsas som uppgifter om verkliga interna
partidiskussioner.

## Designmål

Scenariot är inte en prognos över vem som faktiskt kommer att bilda regering. Det är ett sandbox-test:

> Givet det verkliga mandatresultatet och offentliga utgångspositioner, vilka koalitioner, eftergifter,
> hot, toleranslösningar och strategiska omsvängningar försöker AI-agenterna skapa?

Det gör det särskilt lämpligt för AMAW:s ursprungliga syfte: att se vad autonoma agenter faktiskt gör
när de får olika mål och måste leva med samma institutionella spelregler.
