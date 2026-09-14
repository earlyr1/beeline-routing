# Лайт-ресерч: маршрутизация выездных инженеров (VRPTW + skills + equipment + dynamic)

Дата: 2026-09-14. Собрано 5 параллельными поисковыми агентами, без глубокого чтения статей.

## Выводы (сводка)

1. **Ядро — классический солвер, не LLM и не RL.** Все LLM/нейро-статьи 2024-2026 меряются против PyVRP (HGS) / OR-Tools и обычно проигрывают. На десятках заявок в день OR-Tools решает за 1-2 с.
2. **Формулировка задачи в литературе:** TRSP (Technician Routing and Scheduling Problem, Pillac et al.), WSRP (Workforce Scheduling and Routing), Skill VRP. Динамика — D-TRSP, DWSRP-TW-SC (arXiv 2309.09321).
3. **Скиллы и оборудование = allowed vehicles per node** (OR-Tools `VehicleVar(i).SetValues([...])`, VROOM `skills[]`, Yandex Routing `required_tags`). Матрица «работа → оборудование» предвычисляется в список допустимых инженеров на заявку.
4. **SLA = soft time window с штрафом** (`SetCumulVarSoftUpperBound`), срочная заявка = огромный drop-penalty (`AddDisjunction`). Целевая функция: travel + w_late*минуты опоздания + w_drop*неназначено*priority + w_churn*перемещения относительно опубликованного плана.
5. **Перепланирование = «pin the past, re-optimise the future»** (Timefold continuous planning, D-TRSP): завершённые/текущие визиты фиксируются, старт инженера = текущая позиция/время, дешёвая вставка (Solomon I1) → если нарушает SLA, LNS/ruin-and-recreate 2-5 с с warm start (`ReadAssignmentFromRoutes`). Показать diff до/после.
6. **Объяснимость** — копировать Timefold ScoreAnalysis + Yandex «нераспределённые заказы» + RouteExplainer (arXiv 2403.03585): для каждой заявки список допустимых инженеров и причины исключения остальных, приезд vs окно, дельта времени vs next-best вариант (контрфактический пересчёт), reason-codes для неназначенных. LLM только нарратив поверх структурированных чисел + rule-based fallback.
7. **Где LLM реально полезен:** парсинг новой заявки из свободного текста в JSON-схему, what-if на естественном языке («инженер 3 болеет после 14:00»), объяснения. Архитектура VRPAgent: LLM → структурные ограничения → солвер → валидатор, никогда LLM → маршрут.
8. **Стек для демо:** Python + OR-Tools (или VROOM+OSRM в докере как zero-code альтернатива), OSRM `/table` для матрицы времени (Geofabrik PBF города), DaData / Yandex Geocoder для русских адресов, Leaflet/MapLibre + Gantt по инженерам. Timefold quickstarts (FastAPI + Leaflet) — готовый шаблон. Нейро-CO (RouteFinder, MVMoE, RL4CO) — не тратить время, только слайд «future work».
9. **Кейс уже был на ЛЦТ 2026 (Билайн) и MTS Engineer Hack** — жюри будет сравнивать с коммерческими (Yandex Routing, Veeroute, Relog, Zig-Zag, Salesforce FS). Упор на объяснения, динамику, живую карту.


## Академия: VRPTW со скиллами (TRSP / WSRP / Skill VRP)

- **An Optimization Framework for a Dynamic Multi-Skill Workforce Scheduling and Routing Problem with Time Windows and Synchronization Constraints (DWSRP-TW-SC)** (2023, arxiv)  
  https://arxiv.org/abs/2309.09321  
  MIP formulation plus an ALNS heuristic for a multi-skill workforce routing problem with time windows where tasks arrive in real time. New requests trigger immediate re-optimization, with a 'frozen period' that keeps the already-started / near-term part of each technician's schedule fixed to limit disruption.  
  _Зачем нам:_ Closest single match to the hackathon spec: multi-skill assignment + time windows + dynamic arrivals. The frozen-horizon re-planning pattern (fix the current job and next N minutes, re-optimize the rest) is exactly what 'automatically re-plan the day when an urgent request arrives' needs.
- **On the Dynamic Technician Routing and Scheduling Problem (D-TRSP) / A parallel matheuristic for the TRSP** (2011/2013, paper)  
  https://link.springer.com/article/10.1007/s11590-012-0567-4  
  Pillac, Guéret, Medaglia define the TRSP: technicians with heterogeneous skills, tools and spare parts serve requests with time windows; a parallel ALNS (pALNS) builds the initial plan and re-optimizes when new requests arrive during the day. ALNS gaps vs. MIP optimum are below 1%.  
  _Зачем нам:_ The canonical TRSP formulation. Its skill/tool/spare-part compatibility constraints map 1:1 onto 'qualification + equipment compatibility matrix', and the D-TRSP variant is the reference model for intra-day re-planning with new requests.
- **The Skill Vehicle Routing Problem (Skill VRP)** (2011, paper)  
  https://link.springer.com/chapter/10.1007/978-3-642-21527-8_40  
  Cappanera, Gouveia, Scutellà: each technician has a skill level, each request requires a minimum level; a technician may serve a request only if skill >= requirement; tours start/end at a depot, minimizing routing cost. Compact MIP formulations.  
  _Зачем нам:_ The simplest formal model for 'skills as feasibility constraint'. Good for the MIP / OR-Tools baseline: model skills as allowed-vehicle sets per node (routing.SetAllowedVehiclesForIndex) rather than as costs.
- **Workforce Scheduling and Routing Problems: literature survey and computational study** (2016, paper)  
  https://link.springer.com/article/10.1007/s10479-014-1687-2  
  Castillo-Salazar, Landa-Silva, Qu survey WSRP (technicians, home care, security rounds): common features (time windows, skills/qualifications, working regulations, connected activities, priorities) and solution methods (MIP, CP, ALNS, GA, tabu), plus a computational study showing MIP only scales to small instances.  
  _Зачем нам:_ Gives the vocabulary and constraint taxonomy to describe the product; confirms that for dozens of requests/day heuristics (ALNS/ILS) or CP-SAT/OR-Tools, not pure MIP, are the pragmatic choice.
- **Enhanced Iterated Local Search for the Technician Routing and Scheduling Problem** (2023, arxiv)  
  https://arxiv.org/abs/2303.13532  
  eILS combining local-search operators with removal-repair (ruin & recreate) heuristics tailored to TRSP, four perturbation mechanisms and an elite set; improves best-known solutions on 34/56 benchmark instances at reasonable run time.  
  _Зачем нам:_ A concrete, implementable metaheuristic recipe (ruin-and-recreate + local search) that is easy to code in Python for ~50 requests and runs in seconds - ideal for a hackathon re-planning loop.
- **Decision support for the Technician Routing and Scheduling Problem** (2022, arxiv)  
  https://arxiv.org/abs/2211.16968  
  Column-generation matheuristic for TRSP with qualifications, time constraints and routing costs, applied to real telecom data; also used as a what-if tool for staffing and skill-upgrade decisions. Increased task coverage and cut travel time ~16%.  
  _Зачем нам:_ Shows the 'decision support' framing (what-if: add a skill, add an engineer) that judges like; also a real-industry instance shape (telecom field service) similar to the test data.
- **A Multiperiod Workforce Scheduling and Routing Problem** (2020, arxiv)  
  https://arxiv.org/abs/2008.02849  
  TRSP extension with time windows and heterogeneously skilled technicians over multiple days; constructive heuristics plus an ALNS adaptation; unserved tasks can be postponed to later periods.  
  _Зачем нам:_ Useful if some requests may be pushed to tomorrow: models 'serve today vs. defer with penalty', which is how SLA-priority tiers can be encoded (penalty for postponing high-priority requests).
- **A column-generation approach for an electricity technician routing and scheduling problem with a lexicographic objective** (2026, arxiv)  
  https://arxiv.org/abs/2604.05153  
  Compact MILP and set-packing formulation for an electricity-utility TRSP with a lexicographic objective (first maximize completed intervention duration, then minimize operational cost); compares weighted vs. sequential handling of the hierarchy.  
  _Зачем нам:_ Directly addresses how to encode 'SLA first, travel time second': lexicographic / big-weight objective. Copy the weighting scheme: SLA-violation penalty >> unserved-request penalty >> travel time.
- **Learning State-Dependent Policy Parametrizations for Dynamic Technician Routing with Rework** (2024, arxiv)  
  https://arxiv.org/abs/2409.01815  
  Dynamic technician routing where jobs may need rework; time windows and technician breaks; ALNS embedded in a learned (RL / value-function-approximation) policy that tunes parameters by state, ~10% cost reduction in short runtime.  
  _Зачем нам:_ Shows the modern hybrid pattern: fast ALNS for each re-plan, with a learned policy deciding how much slack/reserve to keep for urgent arrivals. Overkill for a hackathon but a good 'future work' citation.
- **Improving After-sales Service: Deep RL for Dynamic Time Slot Assignment with Commitments and Customer Preferences** (2025, arxiv)  
  https://arxiv.org/abs/2509.17870  
  DRL policy for offering time slots to incoming after-sales service requests while honoring commitments already made to earlier customers, with technician routing evaluated underneath.  
  _Зачем нам:_ Relevant to the 'new request arrives' flow: the notion of commitments (already-promised time windows cannot move) maps to frozen/locked visits when re-planning.
- **Resource Constrained Routing and Scheduling: Review and Research Prospects (CIRRELT)** (2016, paper)  
  https://www.cirrelt.ca/documentstravail/cirrelt-2016-03.cdf  
  Review of routing problems with additional resource constraints (tools, spare parts, vehicle capabilities, skills), classifying models and solution methods.  
  _Зачем нам:_ Reference for the equipment/vehicle dimension: equipment availability is a resource constraint on the vehicle, i.e. a job can only be inserted in a route whose vehicle carries compatible equipment (equipment compatibility matrix = per-node allowed-vehicle set, or a capacity dimension if equipment is loaded at the depot).
- **Vehicle Routing Problem Meets Large Language Models: An Overview and Perspectives** (2026, arxiv)  
  https://arxiv.org/abs/2607.00604  
  Overview of how LLMs are used around VRPs: translating natural-language requests into constraints, generating/tuning heuristics, and explaining solutions to planners.  
  _Зачем нам:_ Supports the 'explain why a route was chosen' feature: run the solver, then feed the structured decision trace (skill match, equipment match, time-window slack, SLA penalties avoided, travel delta) to an LLM for a natural-language explanation - do not let the LLM do the optimization.

**Takeaways агента:**
- Name the problem correctly in your pitch/docs: it is a dynamic Technician Routing and Scheduling Problem (D-TRSP, Pillac et al. 2011/2013) - a VRPTW with heterogeneous technicians, skill and tool/equipment compatibility, and intra-day request arrivals. The 2023 DWSRP-TW-SC paper (arXiv 2309.09321) is the closest academic match; the WSRP survey (Castillo-Salazar 2016) gives the constraint taxonomy.
- Model skills and equipment as hard feasibility (not cost): for each request compute the set of engineers whose skills cover the required skills AND whose vehicle carries compatible equipment (per the compatibility matrix). In OR-Tools this is routing.SetAllowedVehiclesForIndex(node, vehicles); in a MIP it is x[i,k]=0 for incompatible (i,k) as in the Skill VRP (Cappanera et al. 2011).
- Encode SLA priorities lexicographically as in arXiv 2604.05153: objective = W1*sum(SLA lateness or dropped-visit penalty by priority tier) + W2*travel time, with W1 >> W2. In OR-Tools use soft time windows (SetCumulVarSoftUpperBound with a per-priority penalty) plus AddDisjunction(node, drop_penalty_by_priority) so urgent jobs are never silently dropped; the solver then also gives you the numbers for the explanation.
- Re-planning on a new/urgent request: use the frozen-horizon pattern from D-TRSP / DWSRP-TW-SC - lock each engineer's current job and anything starting within the next ~15-30 min, lock any customer-committed windows, then re-run the solver on the rest, warm-started from the current plan (OR-Tools ReadAssignmentFromRoutes + local search with a 2-5 s time limit). First try cheapest feasible insertion of the new request (instant answer), then run ALNS/ILS in the background and swap in the improved plan.
- For dozens of requests, a ruin-and-recreate ALNS/ILS (arXiv 2303.13532, Pillac pALNS) in plain Python converges in seconds: destroy operators = random / worst-cost / related (geographic+time) removal; repair = greedy or regret-2 insertion respecting skills, equipment, time windows. OR-Tools' GUIDED_LOCAL_SEARCH gets you the same in fewer lines - pick one, do not build both.
- Explanation feature: log per-decision facts from the solver (candidates rejected for missing skill / equipment, time-window slack at each stop, SLA penalty avoided, travel-time delta vs. the second-best insertion) and render them as text; optionally pass that structured trace to an LLM for a fluent explanation (VRP-meets-LLM overview, arXiv 2607.00604). Never let the LLM choose the route itself.

## Академия: динамика и перепланирование

- **A Fast Reoptimization Approach for the Dynamic Technician Routing and Scheduling Problem (Pillac, Guéret, Medaglia)** (2018 (based on 2012-2013 work), paper)  
  https://link.springer.com/chapter/10.1007/978-3-319-58253-5_20  
  Defines the Dynamic Technician Routing and Scheduling Problem (D-TRSP) with time windows, skills, tools and spare parts; new requests arrive during the day. Proposes a fast re-optimization loop: parallel Adaptive Large Neighborhood Search (pALNS) plus a Multiple Plan Approach (keep a pool of good plans, commit only the next stop, re-optimize the rest when a request arrives).  
  _Зачем нам:_ This is literally the hackathon problem (skills + tools/equipment + time windows + dynamic requests). The 'commit only what is fixed, re-optimize the open tail' pattern is the practical replanning architecture.
- **Decision support for the Technician Routing and Scheduling Problem** (2022, arxiv) *(дубль)*  
  https://arxiv.org/pdf/2211.16968  
  A decision-support formulation of TRSP with skills and time windows, aimed at dispatchers rather than pure optimization; discusses how to present solutions and trade-offs.  
  _Зачем нам:_ Useful framing for the 'explain why this route' requirement: present per-job assignment reasons, alternatives rejected, and trade-offs, not just a route list.
- **An Optimization Framework for a Dynamic Multi-Skill Workforce Scheduling and Routing Problem with Time Windows and Synchronization Constraints** (2023, arxiv) *(дубль)*  
  https://arxiv.org/pdf/2309.09321  
  Dynamic multi-skill workforce routing where requests appear online; uses a rolling-horizon re-optimization framework with heuristic insertion and neighborhood search to update plans as events happen.  
  _Зачем нам:_ Direct template for an event-driven re-plan loop over a day with multi-skill engineers; shows what to freeze and what to re-optimize.
- **Recent dynamic vehicle routing problems: A survey** (2021, paper)  
  https://www.sciencedirect.com/science/article/abs/pii/S0360835221005088  
  Surveys dynamic VRP variants and solution strategies: periodic vs event-triggered re-optimization, insertion heuristics, rolling horizon, ALNS/LNS, and anticipatory/stochastic methods.  
  _Зачем нам:_ Gives the vocabulary and decision tree (event-triggered re-optimization vs periodic; myopic insertion vs full re-solve) so the team can pick a defensible strategy quickly.
- **Improving After-sales Service: Deep RL for Dynamic Time Slot Assignment with Commitments and Customer Preferences** (2025, arxiv) *(дубль)*  
  https://arxiv.org/pdf/2509.17870  
  DRL policy that decides which time slot to offer/commit to an incoming after-sales service request, with routing feasibility checked by a heuristic insertion; balances commitments already made vs future demand.  
  _Зачем нам:_ Shows the modern hybrid: RL for the accept/slot decision, cheap insertion heuristic for feasibility. For a hackathon, the insertion part is what to copy; the RL part is a stretch goal or an LLM-ranked heuristic.
- **A comparison of reinforcement learning policies for dynamic vehicle routing problems with stochastic customer requests** (2025, paper)  
  https://www.sciencedirect.com/science/article/pii/S0360835224008696  
  Compares four RL policy families for DVRP with online requests against classical insertion/re-optimization baselines; RL gains are modest and training-heavy.  
  _Зачем нам:_ Justifies NOT doing RL in 1-3 days: well-tuned insertion + LNS re-optimization is competitive and far easier to explain to judges.
- **The EURO Meets NeurIPS 2022 Vehicle Routing Competition (Kool et al.)** (2023, paper)  
  https://proceedings.mlr.press/v220/kool23a.html  
  Competition on static VRPTW and a dynamic variant where orders arrive in epochs; winning entries used HGS/PyVRP-style static solvers per epoch plus a learned or rule-based 'dispatch now vs postpone' filter. Quickstart repo: github.com/ortec/euro-neurips-vrp-2022-quickstart.  
  _Зачем нам:_ Canonical, code-available reference for the 'epoch/rolling-horizon re-solve with a strong static solver' pattern; the quickstart contains a working dynamic simulation loop you can mimic.
- **PyVRP: a high-performance VRP solver package** (2024 (arXiv 2403.13795), tool)  
  https://github.com/PyVRP/PyVRP  
  pip-installable Python solver (hybrid genetic search, C++ core) supporting time windows, service durations, release times, shifts, heterogeneous vehicles, and client-vehicle compatibility (site-dependent VRP) via vehicle profiles/allowed groups.  
  _Зачем нам:_ Fastest way to get a strong VRPTW solver; skills/equipment compatibility can be encoded as allowed-vehicle sets per client. Sub-second solves on dozens of jobs make full re-solve on every new request feasible.
- **Google OR-Tools: Vehicle Routing Problem with Time Windows** (2024, tool)  
  https://developers.google.com/optimization/routing/vrptw  
  OR-Tools routing library with time dimension, time windows, disjunctions (dropping visits with penalty), per-vehicle allowed nodes (VehicleVar().SetValues) for skills/equipment, and local search metaheuristics (GLS, tabu).  
  _Зачем нам:_ Most popular hackathon choice; SLA violations map to soft time windows (SetCumulVarSoftUpperBound) and unassigned-visit penalties (AddDisjunction), skills map to VehicleVar restrictions. Time-limited re-solve from a hint (ReadAssignmentFromRoutes) gives warm-started replanning.
- **Timefold Field Service Routing: Skills and Constraints docs** (2025, tool)  
  https://docs.timefold.ai/field-service-routing/latest/user-guide/constraints  
  Commercial/open-core solver with a ready field-service model: hard/medium/soft constraint scoring (required skills hard, unnecessary skill level soft, time windows, fairness, coverage areas), score analysis that lists which constraints each visit breaks and by how much, and real-time planning with pinned (already started) visits.  
  _Зачем нам:_ Best reference for explainability: 'constraint justification' per visit is exactly the explanation UI judges want. Even if you use OR-Tools/PyVRP, replicate its hard/medium/soft score breakdown.
- **Enhanced Iterated Local Search for the Technician Routing and Scheduling Problem** (2023, arxiv) *(дубль)*  
  https://arxiv.org/pdf/2303.13532  
  ILS metaheuristic for TRSP with skills and time windows; simple perturb-and-repair operators (remove k jobs, greedy re-insert) achieve near state-of-the-art.  
  _Зачем нам:_ If you write your own solver, this is the simplest competitive design: cheapest-insertion construction + ruin-and-recreate local search, ~200 lines of Python.
- **A New Optimization Algorithm for the VRPTW (Solomon insertion heuristic I1)** (1987/1992 (classic), paper)  
  https://pubsonline.informs.org/doi/10.1287/opre.40.2.342  
  Classic cheapest-insertion heuristic for VRPTW: evaluate each feasible insertion position for a new customer by added travel time plus time-window slack, pick the best; O(n) feasibility checks per route with forward time slack.  
  _Зачем нам:_ The canonical building block for online insertion of a new (urgent) request in milliseconds; also naturally explainable ('inserted between A and B because it adds only 7 min and keeps all windows').

**Takeaways агента:**
- Architecture: static VRPTW solve at start of day, then an event-driven re-plan loop. On a new request: (1) freeze completed and in-progress stops (pin them), (2) try cheapest feasible insertion (Solomon-style) across all skill/equipment-compatible engineers, (3) if urgent or insertion causes SLA violations, run a time-limited (2-5 s) ruin-and-recreate / LNS re-optimization on the open tail, warm-started from the current plan. This is the D-TRSP 'Multiple Plan / commit-next-stop' pattern and what NeurIPS-2022 winners did.
- Solver choice: OR-Tools routing (Python) is the safest 1-3 day path. Encode skills and equipment compatibility as allowed vehicles per node (routing.VehicleVar(index).SetValues), time windows on the time dimension, SLA as soft upper bounds (SetCumulVarSoftUpperBound with per-priority penalty), optional jobs via AddDisjunction with a big penalty, and warm-start replanning with ReadAssignmentFromRoutes + a 2-5 s time limit and GUIDED_LOCAL_SEARCH. PyVRP is a stronger solver but its compatibility modeling is less flexible; skip RL entirely (2025 comparisons show marginal gains for large training cost).
- Explainability: mirror Timefold's hard/medium/soft constraint scoring. For every assignment store a structured 'justification': feasible engineers (with reasons others were excluded: missing skill X, no equipment Y, outside shift), insertion delta in minutes vs the 2-3 next-best alternatives, time-window slack and SLA risk, and, after a re-plan, a diff (which jobs moved, whose ETA changed and by how much). Render this as a per-job card next to the map; optionally have an LLM turn the structured record into a sentence, never let it decide.
- Keep the model deterministic and small: travel matrix from OSRM/Valhalla table API (or haversine*1.3 as fallback), dozens of jobs means full re-solve is under 1 s, so 'always re-solve from a warm start' is fine; stability matters, so add a small penalty for moving already-announced stops (pinning plus a change-cost term) so re-plans do not thrash routes.
- Urgent requests: give them a very high unassigned penalty and a tight due-time soft bound, so the solver prefers displacing low-priority jobs (which then show up in the explanation as 'bumped to accommodate urgent ticket #N, new ETA +40 min, still inside SLA').
- Demo storyline that judges value: show the morning plan on a map (Leaflet + per-engineer color), inject a new urgent request via API/UI button, animate the diff (before/after routes, moved jobs highlighted), and show KPIs (total travel time, SLA violations, jobs unassigned) plus the constraint breakdown; that covers all rubric items with one flow.

## Инструменты и солверы

- **Google OR-Tools Routing: VRPTW, Common Routing Tasks, Resource Constraints (docs)** (2020-2026, tool) *(дубль)*  
  https://developers.google.com/optimization/routing/vrptw  
  Constraint-programming routing library (C++ core, first-class Python/Java/.NET wrappers, Apache-2.0). Time windows via a 'Time' dimension with CumulVar(i).SetRange(a,b); vehicle shifts via start/end node windows; soft time windows via SetCumulVarSoftUpperBound (SLA penalty); optional visits via AddDisjunction(node, penalty); skills/equipment via routing.VehicleVar(index).SetValues([...]) or SetAllowedVehiclesForIndex; capacities/equipment counts via extra dimensions.  
  _Зачем нам:_ The most flexible pure-Python option for the hackathon: every requirement (skills, tool matrix, time windows, shifts, SLA penalties, drop-with-penalty for infeasible requests) maps to a documented primitive. Re-planning: load current plan with ReadAssignmentFromRoutes + SolveFromAssignmentWithParameters, pin visited stops with NextVar/VehicleVar constraints (see issue #1258 / discussion #2850). Solves dozens of stops in <1-5 s with guided local search time limit. Caveat: quality degrades above ~500 visits, irrelevant here.
- **OR-Tools: Forcing certain vehicles to visit certain locations / fixing nodes (GitHub discussion #2850, issue #1258)** (2021-2023, repo)  
  https://github.com/google/or-tools/discussions/2850  
  Community answers showing the idiom for pinning: constrain VehicleVar(index) to one vehicle, and fix sequence with solver.Add(routing.NextVar(a) == b); combined with an initial assignment the solver only re-optimizes unpinned tail of each route.  
  _Зачем нам:_ This is exactly the 'incremental re-plan when an urgent request arrives' mechanism: mark completed/in-progress stops as fixed, set their start time from telemetry as the vehicle start window, re-solve only the remainder.
- **VROOM (Vehicle Routing Open-source Optimization Machine) + vroom-express API** (2024-2026, repo)  
  https://github.com/VROOM-Project/vroom/blob/master/docs/API.md  
  C++20 heuristic solver (BSD-2), JSON in/out over vroom-express (Node) or CLI; talks directly to OSRM/Valhalla/ORS for matrices. Jobs have skills[], multiple time_windows, service, priority (0-100), setup; vehicles have skills[], time_window (shift), capacity, breaks, max_travel_time, start/end. 'Plan mode' (-c) takes fixed route descriptions and computes ETAs treating constraints as soft, reporting violations.  
  _Зачем нам:_ Fastest path to a working demo: run docker vroom + osrm, POST JSON, get routes with geometry to draw on the map. Skills cover both qualifications and equipment (encode each tool as a skill id). Urgent requests: use priority 100 so they are never dropped; re-plan by passing vehicle start = current position/time and omitting done jobs. Weakness: no native 'pin these stops in this order' in solve mode (only plan mode evaluates fixed routes); no explanation output beyond violations/unassigned lists.
- **Timefold Field Service Routing model — Skills and Real-time planning: pinning visits (docs)** (2024-2026, tool)  
  https://docs.timefold.ai/field-service-routing/latest/real-time-planning/real-time-planning-pinning-visits  
  Timefold Solver (Apache-2.0, Java + timefold-solver Python package, successor of OptaPlanner) plus the hosted Field Service Routing model. Supports skills with min level, visit time windows, technician shifts, multi-day, priorities, and real-time planning via block pinning (freeze stops from shift start) or individual visit pinning; constraints are scored (hard/soft) and the score breakdown per constraint is queryable ('explain the score').  
  _Зачем нам:_ Best reference for the domain model and for the 'explain why this route' requirement: Timefold's ScoreAnalysis / constraint-match breakdown gives per-visit justification (e.g. '-30 soft: travel time', '0 hard: skill match'). The open-source vehicle-routing quickstart (github.com/TimefoldAI/timefold-quickstarts, Java and Python) can be adapted; the hosted FSR API is commercial.
- **Timefold blog: Continuous Planning Optimization with Pinning** (2024, blog)  
  https://timefold.ai/blog/continuous-planning-optimization-with-pinning  
  Explains the continuous/real-time planning pattern: keep a published plan, pin what has started or is committed (@PlanningPin), let the solver re-optimise everything else when new work arrives, and use ProblemChange to inject a new visit into a running solver.  
  _Зачем нам:_ Directly describes the workflow for 'new urgent request arrives mid-day'. The pattern (pin past, optimise future, minimise churn vs published plan) is solver-agnostic and should be the design for your replanning endpoint.
- **PyVRP — open-source state-of-the-art VRP solver (HGS) in Python** (2023-2026, repo) *(дубль)*  
  https://github.com/PyVRP/PyVRP  
  Hybrid genetic search solver (C++ core, pip install pyvrp, MIT). Supports time windows, heterogeneous vehicles with shifts (tw_early/tw_late, latest start), multiple load dimensions, mutually exclusive client groups (multiple time windows), optional clients with prizes, release times, reloading. Very strong solution quality on 1000s of visits.  
  _Зачем нам:_ Good if you want top solution quality in Python, but skills must be emulated (per-vehicle-type profiles with a distance matrix whose infeasible pairs are set to a huge value, or load dimensions as suggested in the release notes), and there is no native pinning API — replanning means re-solving the residual problem with vehicle start positions/times. More work than OR-Tools for this feature set.
- **PyVRP in 2024 and 2025 (release retrospective, Niels Wouda)** (2025, blog)  
  https://nielswouda.com/posts/pyvrp-in-2024-and-2025/  
  Summary of 0.8-0.11 features (client groups, multiple load dimensions 'which can model vehicle and driver skills', vehicle latest-start, reloads) and a benchmark note that PyVRP and VROOM scale to thousands of visits while jsprit and OR-Tools degrade past ~500.  
  _Зачем нам:_ Confirms the scale trade-off: for dozens of requests/day OR-Tools or VROOM are perfectly adequate, so pick on feature fit (pinning, explanations) rather than raw solver strength.
- **jsprit — Java toolkit for rich VRPs (GraphHopper)** (2014-2024 (maintenance mode), repo)  
  https://github.com/graphhopper/jsprit/blob/master/WHATS_NEW.md  
  Java ruin-and-recreate metaheuristic, Apache-2.0. Skills via Service.Builder.addRequiredSkill / VehicleImpl.Builder.addSkill, time windows (multiple), vehicle shifts, capacities, breaks, initial routes (VehicleRoutingProblem.Builder.addInitialVehicleRoute) for locking already-planned jobs, pluggable hard/soft constraints.  
  _Зачем нам:_ Canonical example of the skills-as-hard-constraint modelling and of initial-routes pinning; only use if the team is on the JVM. Development is largely stalled compared to Timefold/OR-Tools.
- **Self-Hosted Vehicle Routing Optimization Engines: VROOM vs jsprit vs OR-Tools Compared (Pi Stack)** (2026, blog)  
  https://www.pistack.xyz/posts/2026-06-16-self-hosted-vehicle-routing-optimization-vroom-jsprit-ortools/  
  Practical comparison explicitly framing field service scheduling as VRPTW with technician skills; notes all three support time windows, service durations and skill matching, and contrasts deployment (VROOM = ready HTTP service, jsprit = JVM library, OR-Tools = Python/C++ library).  
  _Зачем нам:_ Quick decision aid for the hackathon: VROOM for zero-code solving via HTTP, OR-Tools when you need custom constraints (equipment matrix, pinning, SLA penalties) in Python.
- **LKH-3 (Helsgaun) — VRPTW-capable Lin-Kernighan solver** (2017-2024, tool)  
  http://webhotel4.ruc.dk/~keld/research/LKH-3/  
  C solver extending LKH to constrained VRPs (CVRPTW, PDPTW, MTSP) via penalty functions; state of the art on benchmark instances. Input is TSPLIB-style files; license is free for academic/non-commercial research use only.  
  _Зачем нам:_ Classic reference and benchmark yardstick, but not practical for the hackathon: no skills/heterogeneous constraints, file-based interface, restrictive license. Mention only as 'what we compared against' if at all.
- **OSRM Table service / Valhalla sources_to_targets / OpenRouteService Matrix (self-hosted travel-time matrices)** (2020-2026, tool)  
  https://valhalla.github.io/valhalla/api/matrix/  
  All three OSM-based engines expose an N x M duration/distance matrix endpoint and run in Docker (osrm/osrm-backend, ghcr.io/valhalla/valhalla, giscience/openrouteservice). OSRM is fastest but needs --max-table-size raised above the default 100; Valhalla adds time-dependent routing and costing options (max_matrix_location_pairs in config); ORS has profile-specific matrix limits in ors-config.  
  _Зачем нам:_ You need a real drive-time matrix for the city (not haversine) both for the solver and for map polylines. Recommended: OSRM with a city PBF extract (Geofabrik), docker run ... osrm-routed --algorithm mld --max-table-size 10000; use /table for the matrix and /route for geometry to draw in Leaflet/MapLibre. VROOM integrates with any of them natively; for OR-Tools just fetch the matrix once and cache it.
- **Direct and reverse geocoding: testing popular solutions (Habr, ru) + DaData Clean/Suggest API** (2020, blog)  
  https://habr.com/ru/articles/505500/  
  Compares geocoding accuracy on Russian addresses across DaData, Yandex Geocoder, Google, Nominatim and others; DaData and Yandex give house-level precision on messy Russian addresses, Nominatim frequently fails on apartment/house formats.  
  _Зачем нам:_ For Russian test addresses: use DaData (suggest/clean API, free tier ~10k/day, Python 'dadata' package) or Yandex Geocoder HTTP API (needs key; free tier 1000/day); 2GIS Geocoder API is the alternative with strong building-level data in Russian cities. Nominatim (self-hosted or public with 1 req/s) only as a free fallback. Pre-geocode the dataset once, cache lat/lon in the DB, and let users override pins on the map.

**Takeaways агента:**
- Pick Google OR-Tools (pip install ortools, Apache-2.0) as the core solver: model time windows with a 'Time' dimension (CumulVar.SetRange), engineer shifts via vehicle start/end windows, SLA as SetCumulVarSoftUpperBound penalties, skills + equipment compatibility as VehicleVar(index).SetValues([allowed engineer ids]) computed from (skill set ⊇ required) AND (vehicle equipment ⊇ job equipment), and unservable/overflow requests as AddDisjunction(node, penalty). Use GUIDED_LOCAL_SEARCH with a 2-5 s time limit; dozens of requests solve instantly.
- Implement re-planning as 'pin the past, re-optimise the future': when a new/urgent request arrives, set each engineer's start node to their current location and time, drop completed stops, pin in-progress/committed stops via solver.Add(NextVar(a)==b) and VehicleVar constraints, warm-start with ReadAssignmentFromRoutes + SolveFromAssignmentWithParameters, and give the urgent job a huge drop penalty. Add a small soft penalty for moving a stop to a different engineer than in the published plan to reduce churn (the Timefold continuous-planning pattern).
- If you want a no-code fallback or a second engine for comparison, run VROOM + OSRM in Docker (docker compose: osrm/osrm-backend with --max-table-size 10000, vroom-project/vroom-docker): jobs.skills for qualifications and equipment ids, jobs.priority=100 for urgent, vehicle.time_window for shifts, and use plan mode (-c) to evaluate/ETA a fixed or manually edited route.
- Get real travel times: download the city PBF from Geofabrik, run OSRM, call /table once per (re)plan (N≈60 -> 3600 cells, milliseconds) and cache; call /route per leg for polylines to draw in Leaflet/MapLibre. Geocode addresses once with DaData or Yandex Geocoder (Russian addresses), store coordinates, and fall back to Nominatim only if keys are unavailable.
- For the 'explain the route' requirement, compute an explanation object per assignment rather than trusting the solver: for each request list the eligible engineers (skill/equipment filter result), why others were excluded, the arrival vs window and slack, travel time delta vs the next-best insertion (evaluate by re-running the cheap insertion cost or a quick re-solve with that request forced onto another engineer), and any soft penalties incurred. Timefold's constraint-match score breakdown is the model to imitate; OR-Tools gives you the raw dimension values (CumulVar time, slack) to build it.
- Avoid LKH-3 (academic-only license, no skills) and jsprit (JVM, stalled) unless you are already on Java; PyVRP is excellent but lacks native skills/pinning, so it costs more integration time than OR-Tools for this feature list. Keep the domain model (Engineer{skills, shift, vehicle, equipment}, Request{required_skills, required_equipment, window, duration, sla_deadline, priority}) solver-agnostic so you can swap OR-Tools and VROOM behind one Plan/Replan API.

## LLM / агентные / нейро-подходы

- **OptiMUS: Scalable Optimization Modeling with (MI)LP Solvers and Large Language Models** (2024 (ICML), paper)  
  https://proceedings.mlr.press/v235/ahmaditeshnizi24a.html  
  LLM agent that turns a natural-language problem description into a MILP model, writes and debugs solver code (Gurobi), evaluates results and iterates. Introduces the NLP4LP benchmark; +67% solved problems vs. plain prompting.  
  _Зачем нам:_ The canonical 'LLM as optimization modeler' paper. For a hackathon it is a warning as much as a template: the LLM builds the whole model from text, which is fragile (follow-ups like OptArgus/ORPilot exist specifically to catch hallucinated constraints). Do not have the LLM formulate your VRPTW; hand-code the model and let the LLM only parse requests/edit parameters.
- **Mathematical discoveries from program search with large language models (FunSearch)** (2024 (Nature), paper)  
  https://www.nature.com/articles/s41586-023-06924-6  
  DeepMind's evolve-a-Python-function loop: an LLM proposes candidate heuristic code, an evaluator scores it, best candidates are fed back as few-shot examples. Found new results in cap-set and online bin packing.  
  _Зачем нам:_ Origin of LLM-generated heuristics. Requires thousands of LLM calls and a fast evaluator; not realistic to run during a 24-48h hackathon, but the mental model (LLM writes a scoring/insertion heuristic, you evaluate offline on your test instances) is reusable in a cut-down form.
- **Evolution of Heuristics (EoH): Towards Efficient Automatic Algorithm Design Using LLMs** (2024 (ICML), arxiv)  
  https://arxiv.org/abs/2401.02051  
  Co-evolves a 'thought' in natural language and its code implementation in a genetic loop; matches FunSearch quality on TSP/bin packing with far fewer LLM calls.  
  _Зачем нам:_ Cheaper than FunSearch; TSP/VRP construction heuristics are a built-in demo. Still an offline algorithm-design tool, not a runtime routing component.
- **ReEvo: Large Language Models as Hyper-Heuristics with Reflective Evolution** (2024 (NeurIPS), paper)  
  https://proceedings.neurips.cc/paper_files/paper/2024/file/4ced59d480e07d290b6f29fc8798f195-Paper-Conference.pdf  
  Evolves heuristics (e.g. ACO heuristic matrices, LNS operators, construction rules) with 'verbal gradients': the LLM compares two heuristics and states in natural language what to improve. Generates SOTA-ish heuristics in ~5 minutes on six CO problems including CVRP/TSP.  
  _Зачем нам:_ The most practical of the LLM-hyper-heuristic family: open-source (github.com/ai4co/reevo), minutes rather than hours. A stretch goal at best: you could evolve a custom insertion/priority heuristic for your SLA-weighted objective offline, but expect a solid hand-written cheapest-insertion + local search to be as good on dozens of jobs.
- **LLaMEA: A Large Language Model Evolutionary Algorithm for Automatically Generating Metaheuristics** (2024/2025 (IEEE TEVC), arxiv)  
  https://arxiv.org/abs/2405.20132  
  Leiden group's generate-evaluate-improve loop where the LLM writes whole metaheuristic algorithms as Python classes, benchmarked on BBOB; discovered algorithms beat CMA-ES/DE in low dimensions. Code at github.com/XAI-liacs/LLaMEA.  
  _Зачем нам:_ Same family as EoH/ReEvo but for whole algorithms rather than one heuristic function; continuous-optimization focus. Cite as context; not something to run in the hackathon.
- **PyVRP+: LLM-Driven Metacognitive Heuristic Evolution for Hybrid Genetic Search in Vehicle Routing Problems** (2026, arxiv)  
  https://arxiv.org/abs/2604.07872  
  Uses an LLM evolution loop to rewrite operators inside PyVRP's Hybrid Genetic Search (crossover, local-search moves, penalties) and reports improvements on standard VRP benchmarks.  
  _Зачем нам:_ Shows where the field has landed: LLMs improve components of a strong classical solver (PyVRP/HGS), they do not replace it. Implication for the team: build on PyVRP or OR-Tools; treat LLMs as a wrapper/assistant.
- **An Agentic Framework with LLMs for Solving Complex Vehicle Routing Problems (VRPAgent line of work)** (2025, arxiv)  
  https://arxiv.org/abs/2510.16701  
  Multi-agent LLM pipeline: one agent interprets the routing intent/constraints from text, others generate or select algorithms (LNS destroy/repair operators), run them, and validate. Related VRPAgent (2025) reports beating SOTA on some VRP benchmarks by evolving LNS operators.  
  _Зачем нам:_ Closest to 'LLM agent wrapping an OR solver'. The reusable idea is the architecture: intent parser -> structured constraints -> solver -> validator, with the LLM never producing the route itself. Directly maps to 'urgent new request arrives as free text -> structured job -> re-solve'.
- **RouteExplainer: An Explanation Framework for Vehicle Routing Problem** (2024 (PAKDD), arxiv)  
  https://arxiv.org/abs/2403.03585  
  Post-hoc explanations for VRP routes: treats a route as a sequence of edge decisions, computes counterfactual routes ('what if this edge were different'), classifies the intention of each edge (time-window driven, distance driven, etc.), and uses GPT-4 to render the comparison as text.  
  _Зачем нам:_ The one paper squarely on 'explain why this route'. Its recipe is cheap to copy: compute the counterfactual (re-solve with a job moved/removed/fixed), diff objective components (travel time, TW slack, SLA penalty, skill/equipment feasibility), then have the LLM narrate the diff. That is a demo-winning feature and it is honest because the numbers come from the solver.
- **RouteFinder: Towards Foundation Models for Vehicle Routing Problems** (2024/2025, arxiv)  
  https://arxiv.org/abs/2406.15007  
  Single attention-model/POMO-style transformer trained with RL on many VRP variants at once (CVRP, VRPTW, open, backhaul, duration limits...), treating each variant as a subset of attributes. Built on RL4CO. Companion work MVMoE (ICML 2024) does the same with mixture-of-experts over 16 variants.  
  _Зачем нам:_ State of neural combinatorial optimization for VRPTW. Honest assessment: they handle only textbook constraints (capacity, TW, open routes); no skills, no equipment matrix, no multi-engineer heterogeneity, and they train on uniform random instances of fixed sizes. For dozens of jobs a classical solver is both better and instant, so NCO is not worth it in a hackathon except as a slide.
- **RL4CO: an Extensive Reinforcement Learning for Combinatorial Optimization Benchmark** (2023-2025, repo)  
  https://arxiv.org/abs/2306.17100  
  PyTorch library (github.com/ai4co/rl4co) implementing Attention Model, POMO, SymNCO, etc. with environments for TSP, CVRP, VRPTW and more; also hosts the awesome-fm4co reading list.  
  _Зачем нам:_ If someone insists on trying neural routing, this is the only way to do it in a day (pretrained checkpoints, VRPTW env). Still cannot express skill/equipment constraints without custom masking code, so treat it as an experiment, not the core.
- **PyVRP: a high-performance VRP solver package** (2024, tool)  
  https://arxiv.org/abs/2403.13795  
  Open-source (MIT) Hybrid Genetic Search solver in C++/Python supporting time windows, heterogeneous vehicles, multiple depots, client groups, optional clients with prizes, release times, and a hard/soft penalty framework. Actively maintained; discussion thread compares it to OR-Tools and VROOM.  
  _Зачем нам:_ The classical baseline that every 2025-2026 LLM/NCO paper measures against and usually loses to on quality. For dozens of jobs it solves in seconds. Engineer skills can be encoded via per-vehicle 'allowed clients' or large penalties; equipment compatibility via vehicle profiles. OR-Tools routing is the alternative if you need arbitrary callback constraints.
- **Vehicle Routing Problem Meets Large Language Models: An Overview and Perspectives** (2026, arxiv) *(дубль)*  
  https://arxiv.org/abs/2607.00604  
  Survey organizing LLM roles in VRP into: (1) natural-language requirement to model/code, (2) heuristic/operator generation, (3) explanation of results, (4) tool-calling agents around classical solvers; discusses hallucination and evaluation gaps.  
  _Зачем нам:_ One-stop map of the space if a teammate wants to write the 'related work' slide; confirms that the production-relevant roles are interface, explanation and tool orchestration rather than solving.
- **Combinatorial Optimization-Enriched Machine Learning to Solve the Dynamic VRPTW (EURO Meets NeurIPS 2022 competition winner)** (2024 (Transportation Science), paper)  
  https://pubsonline.informs.org/doi/abs/10.1287/trsc.2023.0107  
  Winning approach for the dynamic VRPTW competition: a learned model decides which newly arrived requests to dispatch now vs. postpone, then a classical HGS solver (the PyVRP lineage) routes the dispatched set each wave.  
  _Зачем нам:_ The best evidence for how to do 'new request arrives -> re-plan the day': keep the solver classical and re-run it on the current state, with ML/heuristics only deciding what to include. For a hackathon replace the learned dispatcher with a rule (urgent -> insert now; otherwise re-optimize with existing assignments as warm start).

**Takeaways агента:**
- Keep the optimizer classical. Every 2025-2026 LLM/neural paper benchmarks against PyVRP (HGS) or OR-Tools and rarely beats them on quality; for ~dozens of jobs per day these solve in seconds. Use OR-Tools Routing (easiest for callback constraints: skill masks via vehicle-allowed sets, equipment via per-vehicle dimensions, TW via time dimension with soft-penalty for SLA) or PyVRP (better solutions, MIT, supports heterogeneous vehicles + soft/hard penalties). Do not let an LLM formulate the model (the OptiMUS lesson: hallucinated constraints).
- Use the LLM where the papers show it actually pays off: (a) intent parsing of a new free-text request into a structured job (skill, equipment, TW, urgency) with a JSON schema and validation, (b) natural-language what-if ('what if engineer 3 is sick after 14:00?') translated into parameter edits followed by a re-solve, (c) narrating results. This is the VRPAgent / agentic-framework architecture: LLM -> structured constraints -> solver -> validator, never LLM -> route.
- Build the 'why this route' feature the RouteExplainer way and it will be both honest and impressive: for a selected job, re-solve the counterfactual (job moved to another engineer / other position / dropped), compute the diff in travel time, TW slack, SLA penalty, and which hard constraint (skill, equipment, TW) blocks alternatives, then have the LLM turn that structured diff into 2-3 sentences. Also emit a rule-based explanation as fallback so the demo never depends on the LLM.
- Dynamic replanning: copy the EURO-NeurIPS DVRPTW winner's shape. Maintain state (completed visits fixed, in-progress engineer positions as new start nodes, remaining jobs), and on a new request run the solver again with the previous assignment as warm start (OR-Tools ReadAssignmentFromRoutes / PyVRP initial solutions) and a small time limit (1-3 s). Urgent request = hard TW + high SLA penalty; report which existing jobs got shifted and by how much.
- Neural CO (Attention Model, POMO, RouteFinder, MVMoE, RL4CO) is not worth the hackathon hours: pretrained models cover only capacity/TW/open-route variants on uniform random instances, cannot express skill/equipment matrices without custom masking, and lose to PyVRP by a few percent anyway. At most mention it on a slide as 'future work for 1000+ jobs where sub-second inference matters'.
- LLM-generated heuristics (FunSearch, EoH, ReEvo, LLaMEA, PyVRP+) are offline algorithm-design tools needing thousands of evaluations; hype for a hackathon. If you have a spare teammate and want a differentiator, run ReEvo (open source, ~minutes) on your own instances to evolve a job-priority/insertion scoring function for the SLA-weighted objective, and show the evolved code next to the hand-written one. Otherwise a hand-written cheapest-insertion + 2-opt/relocate local search is enough for dozens of jobs.

## Индустрия, RU-рынок, хакатоны

- **Timefold Field Service Routing — Skills constraint (docs)** (2025, tool)  
  https://docs.timefold.ai/field-service-routing/latest/visit-service-constraints/skills  
  Documents the reference constraint model of a commercial CVRPTW+ field-service product: visits declare required skills, vehicles/technicians carry skills, plus time windows, service duration, shifts, breaks, priorities, and pinned visits. Timefold Solver (open-source, Java/Python, successor of OptaPlanner) implements it as local search over a constraint-stream score.  
  _Зачем нам:_ Best public 'spec' for what constraints a field-service scheduler should expose; copy its vocabulary (requiredSkills, vehicle skills, visit priority, timeWindow, shift, freezeTime) directly into your data model and constraint list.
- **Timefold Field Service Routing — Real-time planning (docs)** (2025, tool)  
  https://docs.timefold.ai/field-service-routing/latest/real-time-planning/real-time-planning  
  Describes continuous/real-time replanning: a freezeTime cutoff before which nothing changes, pinning of confirmed visits, minStartTravelTime from the previous plan, and re-solve when an urgent visit arrives so only unstarted visits are reassigned.  
  _Зачем нам:_ Exactly the 'new urgent request arrives, replan the day' requirement; gives a concrete design: freeze past/in-progress visits, pin customer-confirmed ones, re-solve the rest with a warm start.
- **Timefold quickstarts (GitHub) — Vehicle Routing with Time Windows, Python & Java** (2025, repo)  
  https://github.com/TimefoldAI/timefold-quickstarts  
  Runnable open-source quickstarts (Spring/Quarkus and Python FastAPI) with a Leaflet map UI showing vehicle routes, score breakdown and constraint-violation explanations (score analysis per constraint).  
  _Зачем нам:_ Closest open-source template to the hackathon deliverable: REST service + Leaflet map + per-constraint score explanation. Fork the Python VRPTW quickstart and add skills/equipment constraints.
- **Yandex Routing (Яндекс Маршрутизация) — атрибуты заказов / required_tags** (2024, tool)  
  https://yandex.ru/routing/doc/ru/vrp/properties-of-orders  
  API reference for Yandex's VRP solver: orders carry time_window (hard/soft), service_duration_s, required_tags (must match vehicle tags), priority, penalty.drop / penalty.out_of_time; vehicles carry tags, shifts (start/end, breaks, max_mileage), depot.  
  _Зачем нам:_ The de-facto Russian commercial model of the same problem. Tags are exactly your skills+equipment compatibility matrix; shifts with breaks model engineer schedules; penalty.out_of_time is an SLA-violation cost. Reuse its JSON shape as your API contract.
- **Yandex Routing — нераспределённые заказы (why an order was not assigned)** (2024, tool)  
  https://yandex.ru/routing/doc/ru/vrp/undelivered-orders  
  Explains how the solver reports dropped orders with reasons (no vehicle with required tag, hard window unreachable, shift overrun) and how penalties trade off drops vs lateness.  
  _Зачем нам:_ Model for the 'explain why' UI: for every unassigned or late request, report a machine-readable reason code plus the penalty that drove the decision.
- **Yandex Routing — информация о штрафах (penalty model)** (2024, tool)  
  https://yandex.ru/routing/doc/vrp/concepts/more-about-penalties.html  
  Describes the weighted-penalty objective: drop penalties, per-minute lateness penalties, mileage over shift limit, etc., summed into one cost.  
  _Зачем нам:_ Gives concrete weights structure for your objective: travel time + w_late*SLA lateness minutes + w_drop*unassigned + w_pref*soft preferences; tune weights for 'urgent' requests.
- **Veeroute — Планирование работ сервисных инженеров (use case)** (2023, blog)  
  https://veeroute.cloud/challenges/planirovanie-rabot-servisnykh-inzhenerov/  
  Russian SaaS optimizer's field-service page: engineers with different skills, strict appointment windows, high-priority/urgent requests with dynamic rerouting, multi-engineer jobs, contractual SLAs, equipment types with different durations, load balancing. Veeroute also powers Relog's routing.  
  _Зачем нам:_ Confirms the industry feature checklist for CIS market; use it to list constraints in your pitch (skills, windows, urgency, SLA, equipment, balancing) and mention Veeroute/Relog/Zig-Zag/ANT-Logistics as the commercial baseline.
- **Salesforce Field Service — scheduling optimization (Service Objectives, work rules)** (2024, blog)  
  https://www.salesforce.com/blog/field-service-scheduling/  
  Salesforce Enhanced Scheduling & Optimization separates hard 'work rules' (required skills, territory, availability, resource capacity) from weighted 'service objectives' (minimize travel, ASAP, preferred resource, skill level) and shows a score per candidate slot; supports in-day optimization and 'fix overlaps'.  
  _Зачем нам:_ Adopt the rules-vs-objectives split: hard constraints filter candidates, weighted objectives rank them; show the per-objective score for the chosen engineer as the explanation.
- **Google OR-Tools — Vehicle Routing with Time Windows (CVRPTW) guide** (2024, tool)  
  https://developers.google.com/optimization/routing/cvrptw  
  Canonical routing library: RoutingModel with time dimension (time windows, slack/waiting), vehicle start/end times, AddDisjunction(node, penalty) for optional visits, SetAllowedVehiclesForIndex for skill/equipment compatibility, first-solution heuristics + guided local search with a time limit.  
  _Зачем нам:_ Fastest path to a working solver in Python for dozens of requests: skills/equipment = SetAllowedVehiclesForIndex, SLA = soft upper bound on time dimension (SetCumulVarSoftUpperBound), urgent = high drop penalty; re-plan by fixing visited nodes and re-solving with ReadAssignmentFromRoutes as a warm start.
- **Habr — OR-Tools: библиотека для решения задачи VRP (CVRPTW на практике)** (2024, blog)  
  https://habr.com/ru/articles/783754/  
  Russian-language walkthrough of solving CVRPTW with OR-Tools: building distance/time matrices, time dimension, capacity, disjunctions, search parameters and pitfalls.  
  _Зачем нам:_ Practical Russian tutorial your team can follow directly; covers the exact API calls needed for time windows and dropped visits.
- **mannbajpai/VRPTW-with-Visualization (GitHub)** (2023, repo)  
  https://github.com/mannbajpai/VRPTW-with-Visualization  
  Small demo: constraint-programming VRPTW solver with routes rendered on an interactive Leaflet map via Folium.  
  _Зачем нам:_ Minimal example of the map-visualization layer; shows that Folium/Leaflet with per-vehicle colored polylines and timed markers is enough for a hackathon demo.
- **ComNews — «Билайн бизнес» task at ЛЦТ hackathon: route planning service for field engineers** (2026, blog)  
  https://www.comnews.ru/content/246601/2026-07-28/2026-w31/1018/uchastniki-khakatona-lidery-cifrovoy-transformacii-sozdadut-cifrovye-resheniya-dlya-biskes  
  News that Beeline Business posed an 'intelligent web service for planning field-engineer routes' case at the Leaders of Digital Transformation hackathon; no public winning repo surfaced in search.  
  _Зачем нам:_ Shows the case is a recurring Russian hackathon task (Beeline/LCT, MTS Engineer Hack); judges will compare against commercial tools, so emphasize explanations, dynamic replanning and a live map over raw solver quality.

**Takeaways агента:**
- Solver: use Google OR-Tools RoutingModel (Python) for ~dozens of requests; it solves in <1-2 s with GUIDED_LOCAL_SEARCH and a time limit. Map constraints as: skills+equipment compatibility -> SetAllowedVehiclesForIndex(node, [engineer ids]); time windows -> time dimension CumulVar ranges (hard) or SetCumulVarSoftUpperBound (soft SLA with per-minute penalty); optional visits -> AddDisjunction(node, drop_penalty), urgent = very large penalty; engineer shift/breaks -> vehicle start/end times plus FixedDurationIntervalVar breaks. Alternative: Timefold Solver (Python/Java) if you want richer constraint streams and built-in score explanation.
- Copy the commercial data model rather than inventing one: Yandex Routing's orders{time_window, hard_window, service_duration_s, required_tags, priority, penalty.drop/out_of_time} and vehicles{tags, shifts{start,end,breaks,max_mileage}}; Timefold's requiredSkills/vehicle skills/pinned visits. 'Tags' cover both skills and equipment; equipment-job compatibility matrix becomes a precomputed allowed-engineer list per job (engineer has skill AND vehicle carries compatible equipment).
- Objective = weighted penalty sum, following Yandex/Salesforce practice: travel_time + w_late*minutes past SLA deadline + w_drop*unassigned (scaled by priority) + w_pref*soft preferences (e.g. same engineer as last visit). Expose the weights in the UI so demo judges can see the trade-off.
- Dynamic replanning (Timefold pattern): keep a freezeTime = now + buffer; visits completed or in progress are fixed (OR-Tools: put them as fixed prefixes via ReadAssignmentFromRoutes / NextVar constraints), customer-confirmed visits are 'pinned', everything else is re-solved when a new/urgent request arrives; start engineers from their current GPS location, not the depot. Show a diff view: what moved, who got the urgent job, and how many minutes of lateness it cost.
- Explanation UI: emit per-request reason codes like Yandex's undelivered-orders report and Salesforce's per-objective score: for the assigned engineer show 'has skills X, vehicle has equipment Y, arrives 10:40 inside window 10:00-12:00, +12 min travel vs next-best candidate'; for unassigned show 'no available engineer with skill Z / hard window unreachable / shift overflow'. Compute the 'next-best candidate' by cheap what-if re-solves or by insertion-cost deltas.
- Map/UI: Leaflet or MapLibre with per-engineer colored polylines, numbered markers with ETA and window bars, a timeline (Gantt) per engineer; use OSRM/Valhalla (or Yandex/2GIS Distance Matrix if allowed) for the travel-time matrix, with Haversine * 1.3 as offline fallback. Reference Timefold quickstarts for a ready FastAPI+Leaflet layout, and position the product against Salesforce FS / SAP FSM / Yandex Routing / Veeroute / Relog / Zig-Zag / ANT-Logistics in the pitch.
