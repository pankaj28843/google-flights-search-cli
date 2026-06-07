# Flight Search First Principles

This document describes durable flight-search concepts. It must not depend on
Google-generated selectors, DOM indexes, or exact class names.

## Intent

A flight search intent names:

- origin and destination
- trip type
- travel dates or date windows
- passenger party
- cabin
- search context such as language and currency
- ranking preferences
- output fields and evidence requirements

The implementation must preserve the distinction between user intent and the
browser steps needed to express that intent on Google Flights.

## Routes

Routes can be airport-code choices or city/city-like autocomplete choices.
Ambiguous route text must produce candidate choices instead of silent selection.

Multi-airport city context is route-disambiguation evidence. It is not the same
as a result-filter preference for airports.

## Trip Types

Supported by current evidence:

- round trip
- one way

Deferred:

- multi-city live search, until leg-list ordering and encoding are probed

One-way searches must not retain stale return dates.

## Passengers

The passenger party can include:

- adults
- children aged 2-11
- infants in seat
- infants on lap

Current evidence covers one-variable mutations for each passenger category. Any
future count-limit support must be fixture-backed.

## Dates

Concrete departure and return dates are supported by evidence. Date windows are
a CLI service-layer expansion over concrete dates; they are not proof that
Google's own flexible-date control is supported.

Date grid and price graph data may help reduce which date pairs to inspect, but
primary result rows remain the result truth source.

## Cabin And Fare Context

Supported by current evidence:

- economy
- business

Deferred:

- premium economy
- first

Do not infer unprobed cabin encodings from numeric adjacency.

## Ranking And Preferences

The CLI may rank returned results using visible fields such as price, stops,
duration, layovers, carriers, baggage, emissions, and facilities.

Ranking based on airline preference or senior comfort must be an explained
post-result ranking unless a live Google Flights filter has been proven.

Date-window ranking must account for every generated date pair before producing
agent-facing recommendations. Fresh cache observations may support ranking for
at most the configured cache age; missing pairs must be probed, marked
unsupported, or explicitly skipped with evidence instead of hidden behind a
placeholder rank.

## Itinerary Facts

Itinerary output can include visible provider options, baggage text, carriers,
flight numbers, layover details, emissions, cabin facilities, and baggage-policy
links.

Terminal information must be absent or marked `not_found` when not visible.

## Booking Boundary

The CLI can report booking options visible on Google Flights. It must not enter
provider checkout, personal-data, payment, or booking-confirmation flows.
