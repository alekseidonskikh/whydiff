-- Demo data: orders and a copy with known differences.
--
--   whydiff diff $WHYDIFF_PG_URI public.orders $WHYDIFF_PG_URI public.orders_v2 --key id
--
-- Expected: 2 removed, 1 added, 101 changed
-- (created_at shifted by +3 hours in 100 rows, amount off by 0.01 in 1 row).

create table orders (
    id          integer primary key,
    customer    text not null,
    amount      numeric(12, 2),
    status      text,
    created_at  timestamptz not null
);

insert into orders
select
    g,
    'customer_' || (g % 97),
    (g * 7 % 10000) / 100.0,
    (array['new', 'paid', 'shipped', 'cancelled'])[g % 4 + 1],
    timestamptz '2024-01-01 00:00:00+00' + g * interval '1 minute'
from generate_series(1, 200000) as g;

create table orders_v2 as select * from orders;
alter table orders_v2 add primary key (id);

-- a timezone bug in a new pipeline
update orders_v2 set created_at = created_at + interval '3 hours' where id between 5000 and 5099;
-- lost rows
delete from orders_v2 where id in (17, 18);
-- an extra row
insert into orders_v2 values (300001, 'customer_new', 1.00, 'new', '2024-06-01 00:00:00+00');
-- a rounding difference
update orders_v2 set amount = amount + 0.01 where id = 42;

analyze orders;
analyze orders_v2;
