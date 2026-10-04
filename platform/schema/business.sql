-- Business schema for SMB tenant databases (lane p1-business-schema).
-- Applied by platform/schema/smb_schema.py initialize() against an identity-only
-- database whose tenant_identity row is owned by the client registry; this script
-- declares business tables only.
--
-- Every business table carries the provenance columns first (id, client_id,
-- source_system, source_id, imported_at, import_batch_id), then its payload
-- columns, then a table-level source identity uniqueness constraint and a
-- tenant_identity reference so rows from other tenants are rejected by SQLite.

CREATE TABLE customers (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    name TEXT NOT NULL,
    email TEXT,
    UNIQUE(source_system, source_id)
);

CREATE TABLE vendors (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    name TEXT NOT NULL,
    email TEXT,
    UNIQUE(source_system, source_id)
);

CREATE TABLE items (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    unit_cost_cents INTEGER NOT NULL CHECK (typeof(unit_cost_cents) = 'integer' AND unit_cost_cents >= 0),
    unit_price_cents INTEGER NOT NULL CHECK (typeof(unit_price_cents) = 'integer' AND unit_price_cents >= 0),
    UNIQUE(source_system, source_id),
    UNIQUE(sku)
);

CREATE TABLE locations (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    name TEXT NOT NULL,
    UNIQUE(source_system, source_id)
);

CREATE TABLE stock_levels (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    item_id TEXT NOT NULL REFERENCES items(id),
    location_id TEXT NOT NULL REFERENCES locations(id),
    quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity >= 0),
    reorder_point INTEGER NOT NULL CHECK (typeof(reorder_point) = 'integer' AND reorder_point >= 0),
    UNIQUE(source_system, source_id),
    UNIQUE(item_id, location_id)
);

CREATE TABLE sales_orders (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    customer_id TEXT REFERENCES customers(id),
    location_id TEXT NOT NULL REFERENCES locations(id),
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed', 'cancelled')),
    total_cents INTEGER NOT NULL CHECK (typeof(total_cents) = 'integer' AND total_cents >= 0),
    UNIQUE(source_system, source_id)
);

CREATE TABLE sales_order_lines (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    sales_order_id TEXT NOT NULL REFERENCES sales_orders(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (typeof(unit_price_cents) = 'integer' AND unit_price_cents >= 0),
    UNIQUE(source_system, source_id)
);

CREATE TABLE purchase_orders (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    vendor_id TEXT NOT NULL REFERENCES vendors(id),
    location_id TEXT NOT NULL REFERENCES locations(id),
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'ordered', 'received', 'cancelled')),
    total_cents INTEGER NOT NULL CHECK (typeof(total_cents) = 'integer' AND total_cents >= 0),
    UNIQUE(source_system, source_id)
);

CREATE TABLE purchase_order_lines (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    purchase_order_id TEXT NOT NULL REFERENCES purchase_orders(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity > 0),
    unit_cost_cents INTEGER NOT NULL CHECK (typeof(unit_cost_cents) = 'integer' AND unit_cost_cents >= 0),
    UNIQUE(source_system, source_id)
);

CREATE TABLE invoices (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    sales_order_id TEXT REFERENCES sales_orders(id),
    issued_at TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'paid', 'void')),
    total_cents INTEGER NOT NULL CHECK (typeof(total_cents) = 'integer' AND total_cents >= 0),
    UNIQUE(source_system, source_id)
);

CREATE TABLE payments (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    invoice_id TEXT NOT NULL REFERENCES invoices(id),
    paid_at TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents >= 0),
    method TEXT NOT NULL,
    UNIQUE(source_system, source_id)
);

CREATE TABLE interactions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    client_id TEXT NOT NULL REFERENCES tenant_identity(client_id),
    source_system TEXT NOT NULL CHECK (length(source_system) > 0),
    source_id TEXT NOT NULL CHECK (length(source_id) > 0),
    imported_at TEXT NOT NULL,
    import_batch_id TEXT NOT NULL CHECK (length(import_batch_id) > 0),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    occurred_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    notes TEXT NOT NULL,
    UNIQUE(source_system, source_id)
);
