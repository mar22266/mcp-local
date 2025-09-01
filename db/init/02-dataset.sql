--  USERS / ORDERS / ORDER_ITEMS
-- 3 tablas, cada una con 6 columnas

BEGIN;

CREATE TABLE IF NOT EXISTS public.users (
  id           BIGSERIAL PRIMARY KEY,
  email        TEXT NOT NULL UNIQUE,
  country      TEXT NOT NULL,
  created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
  status       TEXT NOT NULL,
  plan         TEXT NOT NULL
);

TRUNCATE TABLE public.users RESTART IDENTITY;

INSERT INTO public.users (email, country, created_at, status, plan)
SELECT
  'user' || g || '@mail.com' AS email,
  CASE WHEN g % 3 = 0 THEN 'GT' WHEN g % 3 = 1 THEN 'SV' ELSE 'HN' END AS country,
  NOW() - (FLOOR(RANDOM() * 365)::INT || ' DAYS')::INTERVAL AS created_at,
  CASE WHEN g % 5 < 3 THEN 'ACTIVE' WHEN g % 5 = 3 THEN 'INACTIVE' ELSE 'BANNED' END AS status,
  CASE WHEN g % 4 < 2 THEN 'FREE' WHEN g % 4 = 2 THEN 'PRO' ELSE 'BIZ' END AS plan
FROM GENERATE_SERIES(1, 20000) AS g;

CREATE INDEX IF NOT EXISTS idx_users_country ON public.users (country);
CREATE INDEX IF NOT EXISTS idx_users_country_email ON public.users (country, email);

CREATE TABLE IF NOT EXISTS public.orders (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT NOT NULL REFERENCES public.users(id),
  created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
  total_cents  INTEGER  NOT NULL,
  status       TEXT     NOT NULL,
  channel      TEXT     NOT NULL
);

TRUNCATE TABLE public.orders RESTART IDENTITY;

WITH u AS (
  SELECT COALESCE(MAX(id), 0) AS max_id FROM public.users
)
INSERT INTO public.orders (user_id, created_at, total_cents, status, channel)
SELECT
  1 + FLOOR(RANDOM() * u.max_id)::INT AS user_id,
  NOW()
    - (FLOOR(RANDOM() * 180)::INT   || ' DAYS')::INTERVAL
    - (FLOOR(RANDOM() * 86400)::INT || ' SECONDS')::INTERVAL AS created_at,
  (500 + FLOOR(RANDOM() * 9500)::INT) * (1 + FLOOR(RANDOM() * 3)::INT) AS total_cents,
  COALESCE(
    (ARRAY['CREATED','PAID','SHIPPED','CANCELLED'])[1 + FLOOR(RANDOM() * 4)::INT]::TEXT,
    'CREATED'
  ) AS status,
  COALESCE(
    (ARRAY['WEB','MOBILE','STORE','PARTNER'])[1 + FLOOR(RANDOM() * 4)::INT]::TEXT,
    'WEB'
  ) AS channel
FROM u, GENERATE_SERIES(1, 120000) AS g
WHERE u.max_id > 0; 

CREATE INDEX IF NOT EXISTS idx_orders_user_created_at_desc
  ON public.orders (user_id, created_at DESC) INCLUDE (id);
CREATE INDEX IF NOT EXISTS idx_orders_created_at
  ON public.orders (created_at);

CREATE TABLE IF NOT EXISTS public.order_items (
  id          BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL REFERENCES public.orders(id),
  product     TEXT   NOT NULL,
  qty         INTEGER NOT NULL CHECK (qty > 0),
  unit_cents  INTEGER NOT NULL,
  category    TEXT   NOT NULL
);


TRUNCATE TABLE public.order_items RESTART IDENTITY;

INSERT INTO public.order_items (order_id, product, qty, unit_cents, category)
SELECT
  o.id AS order_id,
  'PRODUCT-' || (1 + FLOOR(RANDOM() * 999)::INT) AS product,
  1 + FLOOR(RANDOM() * 4)::INT AS qty,
  100 + FLOOR(RANDOM() * 9900)::INT AS unit_cents,
  (ARRAY['ELEC','HOME','TOY','SPORT'])[1 + FLOOR(RANDOM() * 4)::INT] AS category
FROM public.orders AS o
JOIN LATERAL GENERATE_SERIES(1, 1 + FLOOR(RANDOM() * 3)::INT) AS it(i) ON TRUE;

CREATE INDEX IF NOT EXISTS idx_items_order_id         ON public.order_items (order_id);
CREATE INDEX IF NOT EXISTS idx_items_order_product    ON public.order_items (order_id, product);

ANALYZE;

COMMIT;
