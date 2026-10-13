-- Single source of truth for every metric (design review 5A). Every consumer (memos, simulator,
-- chat, PDF) reads these views; nothing recomputes metrics on its own.
-- Inputs (created by build.py): folders (latest snapshot per buyer_key+folder), results (awards from
-- that snapshot, carrying buyer_key). buyer_key = NIF, else DIR3, else normalised name + parent chain:
-- feed 1044 never publishes NIFs, so NIF alone would merge different buyers' folders.
-- Remaining inputs:
-- that latest snapshot), snapshots (all snapshots), brand_regex(pattern), and the Python UDF
-- ext_months(text). build.py materialises the views into tables so readers need no UDFs.

-- Lot budgets from the folder's lot list (multi-lot tenders publish one budget per lot).
create or replace view v_lot_budgets as
select f.buyer_key, f.folder_id, l.lot_id, l.budget_net as lot_budget
from folders f,
     unnest(from_json(f.lots_json, '[{"lot_id":"VARCHAR","budget_net":"DOUBLE"}]')) as t(l)
where f.lots_json is not null;

-- One row per awarded lot with all derived fields. Award filter + procedure codes defined ONCE here
-- (build.py mirrors the award codes when it unions awards across snapshots).
create or replace view v_lots as
with base as (
  select
    r.*,
    f.buyer_nif as folder_buyer_nif,
    f.title, f.buyer, f.buyer_dir3, f.buyer_parents, f.is_health, f.source_feed,
    -- display and matching never depend on PLACSP's whitespace or spelling of the winner
    trim(regexp_replace(coalesce(f.title, ''), '\s+', ' ', 'g')) as title_clean,
    -- Feed 1044 publishes no AwardDate on results. The contract date, then the award-notice date on the
    -- folder, stand in for it; award_date_source says which, so time-based analyses can restrict.
    case when try_cast(r.award_date as date) is not null then 'award_date'
         when try_cast(left(r.contract_date, 10) as date) is not null then 'contract_date'
         when try_cast(f.notice_award as date) is not null then 'notice_award' end as award_date_source,
    f.cpvs, left(f.cpvs, 4) as cpv4, left(f.cpvs, 2) as cpv2,
    f.procedure, f.contract_type, f.n_lots, f.nuts, f.link, f.notice_cn, f.submission_deadline,
    f.extension_desc, f.extension_period, f.attachments_json,
    -- A lot inherits the folder budget only when it is the folder's only awarded result. Regional
    -- platforms (feed 1044) publish no lot list, so a folder with several results and n_lots = 0 is a
    -- multi-lot tender whose per-lot budgets are unknown: comparing each award with the whole folder
    -- budget invents a ~76 % "discount" at every bid count.
    coalesce(lb.lot_budget,
             case when f.n_lots <= 1 and count(*) over (partition by r.buyer_key, r.folder_id) = 1
                  then f.budget_net end) as lot_budget,
    case f.duration_unit
      when 'MON' then f.duration when 'ANN' then f.duration * 12
      when 'DAY' then f.duration / 30.4 when 'WEE' then f.duration / 4.35 end as duration_months,
    coalesce(try_cast(r.award_date as date), try_cast(left(r.contract_date, 10) as date),
             try_cast(f.notice_award as date)) as award_d,
    try_cast(left(r.contract_date, 10) as date) as contract_d,
    try_cast(f.notice_cn as date) as notice_d,
    try_cast(f.submission_deadline as date) as deadline_d,
    try_cast(f.planned_start as date) as planned_start_d,
    try_cast(f.planned_end as date) as planned_end_d
  from results r
  join folders f using (buyer_key, folder_id)
  left join v_lot_budgets lb
    on lb.buyer_key = r.buyer_key and lb.folder_id = r.folder_id and lb.lot_id = r.lot_id
  where r.result_code in ('2', '8', '9', '11')      -- awarded / finally awarded / formalised
)
select *,
  procedure in ('1', '9') as is_open,              -- "open" = open + simplified open, everywhere
  case when lot_budget > 0 and award_net > 0 and award_net / lot_budget between 0.05 and 1.05
       then 1 - award_net / lot_budget end as discount,
  case when lot_budget < 15000 then 'a<15k' when lot_budget < 100000 then 'b<100k'
       when lot_budget < 500000 then 'c<500k' when lot_budget is not null then 'd500k+' end as band,
  lot_budget * 12 / nullif(duration_months, 0) as annual_budget,
  deadline_d - notice_d as deadline_days,
  regexp_matches(lower(title_clean), (select pattern from brand_regex)) as brand_in_title,
  coalesce(planned_start_d, contract_d, award_d + interval 30 day) as est_start
from base;

-- Rate-eligible lots: open procedures with a bid count (single-bid rates, peers, curves).
create or replace view v_rate_lots as
select * from v_lots where is_open and n_bids is not null;

-- Contract end dates. "Start re-tender by" uses end_initial (extensions are not published and may
-- not be exercised); end_max is informational.
create or replace view v_contract_terms as
select *,
  cast(coalesce(planned_end_d, est_start + to_months(cast(round(duration_months) as int))) as date) as end_initial,
  cast(est_start + to_months(cast(round(duration_months + coalesce(ext_months, 0)) as int)) as date) as end_max
from (select *, coalesce(ext_months(extension_period::varchar), ext_months(extension_desc::varchar)) as ext_months from v_lots);

-- Empirical lead time from call for tenders to award, by CPV-2 (open procedures, plausible range).
create or replace view v_lead_times as
select cpv2, count(*) as n,
  median(award_d - notice_d) as med_days,
  quantile_cont(award_d - notice_d, 0.8) as p80_days
from v_rate_lots
where notice_d is not null and award_d > notice_d and award_d - notice_d < 1000
group by cpv2 having count(*) >= 20;

-- Peer benchmark cells: same CPV-4 and value band, health buyers, open procedures, 2017+.
create or replace view v_peers as
select cpv4, band, count(*) as n,
  avg((n_bids = 1)::int) as single_bid_rate,
  median(n_bids) as median_bids,
  median(discount) as median_discount
from v_rate_lots
where is_health and award_d >= date '2017-01-01'
group by cpv4, band;
