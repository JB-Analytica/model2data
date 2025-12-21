# Examples

This directory contains example DBML files that showcase different use cases and features of `model2data`.

Each example demonstrates best practices and different aspects of data modeling and synthetic data generation.

---

## Quick Start: Run Any Example

```bash
# Generate data from an example
model2data generate --file examples/{filename}.dbml --rows 100 --seed 42

# This creates a `dbt_{project_name}/` directory with:
# - Synthetic data (seeds/ folder with CSV files)
# - A complete dbt project scaffold
# - Staging models and dbt configuration

cd dbt_{project_name}
dbt deps
dbt seed
dbt run
```

---

## Examples Overview

### 1. **hackernews.dbml** — Data Lake / DLT Pipeline Pattern
- **Domain**: News aggregation platform (HackerNews-like)
- **Focus**: DLT (Data Load Tool) tracking tables, nested relationships
- **Features Showcased**:
  - DLT metadata tables (`_dlt_loads`, `_dlt_version`, `_dlt_pipeline_state`)
  - Nested data structures (`stories__kids` array children)
  - Complex reference types (UUID PKs, many-to-many patterns)
  - DLT-specific schema patterns

**Use case**: If you're building data lakes or using DLT-like pipelines, this shows how `model2data` handles metadata and nested structures.

**Try it**:
```bash
model2data generate --file examples/hackernews.dbml --rows 200 --seed 42
cd dbt_hackernews
dbt seed && dbt run
```

---

### 2. **ecommerce.dbml** — E-Commerce / Retail Platform
- **Domain**: Online retail store
- **Focus**: Transactional data, multi-table relationships, Faker data types, min/max constraints
- **Features Showcased**:
  - Faker provider data types (email, first_name, last_name, phone_number, country, word, ean13)
  - Customer master data with realistic naming and contact info
  - Product catalog with inventory tracking
  - Order-to-fulfillment workflows (orders → order_items)
  - Customer reviews and ratings with min/max constraints
  - Business constraints: unique emails, foreign key relationships
  - **Inline note constraints**: Price ranges, stock levels, quantities, ratings

**Tables**: 5 tables with 5 relationships
- `customers` — Customer directory with realistic names, emails, phone numbers
- `products` — Product catalog with constrained prices (5.99-999.99) and stock levels (0-1000)
- `orders` — Order headers with total amount constraints (10-5000)
- `order_items` — Line items with quantity (1-10) and unit price constraints
- `product_reviews` — Customer reviews with ratings (1-5)

**Advanced Features**:
- **Faker data types**: Uses `email`, `first_name`, `last_name`, `phone_number`, `country`, `word`, `ean13`
- **Inline note constraints**: `price numeric [not null, note: '{"min": 5.99, "max": 999.99}']`
- **Foreign keys**: Transactional relationships between customers, orders, and products
- **Unique constraints**: Email uniqueness per customer
- **Realistic data generation**: Names, emails, phone numbers generated via Faker library

**Use case**: Perfect for learning how to model transactional systems. Run this to generate realistic e-commerce data for testing, analytics, or building dbt pipelines.

**Try it**:
```bash
model2data generate --file examples/ecommerce.dbml --rows 500 --seed 42
cd dbt_ecommerce

# Explore the generated data
head -20 seeds/ecommerce/customers.csv
head -20 seeds/ecommerce/orders.csv

# Run dbt
dbt seed
dbt run

# Query the generated data
dbt run-operation select_from_seed --args '{"table": "customers"}'
```

---

### 3. **saas_platform.dbml** — SaaS / Multi-Tenant Application
- **Domain**: Multi-tenant SaaS platform
- **Focus**: Multi-tenancy patterns, Faker data types, audit trails, min/max constraints
- **Features Showcased**:
  - Faker provider data types (company, slug, email, first_name, last_name, word)
  - Multi-tenancy: Organization-based isolation
  - User hierarchies with role-based access
  - Subscription and billing tracking with price constraints
  - API usage monitoring with realistic limits and constraints
  - Audit logging of events
  - **Inline note constraints**: Pricing, feature limits, usage tracking

**Tables**: 7 tables with 8 relationships
- `organizations` — Tenant root entity (using `company` type for realistic names)
- `users` — Users per org with `email`, `first_name`, `last_name` types
- `subscriptions` — Billing subscriptions with monthly price constraints (0-999)
- `invoices` — Invoice tracking with amount constraints (0-999)
- `feature_limits` — API calls (1K-1M), storage (5-1000 GB), users (1-500) with realistic constraints
- `api_usage` — API call tracking with month constraints (1-12) and usage limits (0-1M)
- `audit_events` — Audit trail of system events

**Advanced Features**:
- **Faker data types**: `company`, `slug`, `email`, `first_name`, `last_name`, `word`
- **Multi-tenancy**: Every table references `organization_id` for isolation
- **Inline note constraints**: `month int [not null, note: '{"min": 1, "max": 12}']`
- **Foreign keys**: Nested relationships (organizations → users, subscriptions, audit_events)
- **Status tracking**: Subscription and invoice status enumeration
- **Realistic SaaS metrics**: API limits, storage quotas, user caps per plan tier

**Use case**: Essential for building SaaS platforms, understanding multi-tenancy patterns, subscription billing, and compliance. Run this to generate realistic tenant, user, subscription, and billing data.

**Try it**:
```bash
model2data generate --file examples/saas_platform.dbml --rows 100 --seed 42
cd dbt_saas_platform

# Explore multi-tenant structure
head -20 seeds/saas_platform/organizations.csv
head -20 seeds/saas_platform/users.csv

# Explore billing
head -20 seeds/saas_platform/subscriptions.csv
head -20 seeds/saas_platform/invoices.csv

dbt seed
dbt run

# You can now build dbt models for:
# - Multi-tenant user funnels
# - Cohort analysis per org
# - Billing/revenue analytics
# - Feature adoption tracking
```

---

### 4. **advanced_features.dbml** — HR / Project Management System
- **Domain**: Employee management and project allocation
- **Focus**: Self-referential relationships, min/max constraints, diverse Faker data types
- **Features Showcased**:
  - Faker provider data types (first_name, last_name, email, word)
  - Self-referential foreign keys (employees.manager_id → employees.id)
  - **Inline note constraints** for realistic data bounds across all numeric fields
  - Multiple numeric types (bigint, int, numeric) with constraints
  - Complex multi-table relationships (employees → projects → time entries)

**Tables**: 7 tables with 11 relationships
- `departments` — Department master with `word` type and budget constraints (100K-5M)
- `employees` — Employee directory with self-referential manager hierarchy and salary constraints (30K-200K)
- `projects` — Project master with budget (50K-2M) and priority constraints (1-5)
- `project_assignments` — Employee-to-project mappings with allocation % constraints (0-100)
- `time_entries` — Daily time tracking with hours constraints (0.5-8 per day)
- `expenses` — Project expense tracking with amount constraints (100-50K) and approval workflow
- `performance_ratings` — Performance reviews with rating constraints (1-5)

**Advanced Features**:
- **Self-referential FK**: `employees.manager_id → employees.id` (builds org hierarchies)
- **Faker data types**: `first_name`, `last_name`, `email` for realistic employee data, `word` for project/department names
- **Inline note constraints**:
  - Budget amounts: Department (100K-5M), Project (50K-2M), Salary (30K-200K)
  - Priority: 1-5
  - Allocation: 0-100%
  - Hours worked: 0.5-8 per day
  - Rating: 1-5
  - Expenses: 100-50,000
- **Complex hierarchies**: Departments → Employees (with managers) → Projects → Time tracking
- **Multiple relationship types**: Parent-child (dept→emp), hierarchy (mgr→emp), assignment (emp→proj)

**Use case**: Learn how to model organizational data, complex relationships, and min/max constraints. Run this to generate HR/payroll data, project allocation data, and performance tracking.

**Try it**:
```bash
model2data generate --file examples/advanced_features.dbml --rows 50 --seed 123
cd dbt_advanced_features

# Explore hierarchical data
head -20 seeds/advanced_features/employees.csv

# Explore projects and time tracking
head -20 seeds/advanced_features/projects.csv
head -20 seeds/advanced_features/time_entries.csv

# Explore performance reviews
head -20 seeds/advanced_features/performance_ratings.csv

dbt seed
dbt run

# Now you can build dbt models for:
# - Org charts and reporting lines
# - Project profitability (budget vs spent vs time tracked)
# - Performance evaluation aggregations
# - Expense approval workflows
```

---

## Feature Matrix

| Feature | hackernews | ecommerce | saas_platform | advanced_features |
|---------|-----------|-----------|---------------|-------------------|
| **Foreign Keys** | ✓ | ✓ | ✓ | ✓ |
| **Self-referential FKs** | - | - | - | ✓ (manager hierarchy) |
| **Unique Constraints** | ✓ | ✓ | ✓ | - |
| **Inline Note Constraints** | - | ✓ | ✓ | ✓ |
| **Faker Data Types** | - | ✓ | ✓ | ✓ |
| **Multi-tenancy** | - | - | ✓ | - |
| **Nested Tables** | ✓ | - | - | - |
| **Date Types** | - | ✓ | ✓ | ✓ |
| **Boolean Flags** | - | ✓ | ✓ | - |

---

## Understanding Inline Note Constraints

The examples showcase **inline note constraints** for controlling generated data ranges. This is done using JSON in the column definition:

```dbml
Table products {
  id bigint [pk, not null]
  price numeric [not null, note: '{"min": 5.99, "max": 999.99}']
  stock_quantity int [not null, note: '{"min": 0, "max": 1000}']
  rating int [note: '{"min": 1, "max": 5}']
}
```

### Supported Constraint Types

**Integer constraints:**
```dbml
age int [note: '{"min": 18, "max": 65}']
priority int [note: '{"min": 1, "max": 5}']
quantity int [note: '{"min": 1, "max": 100}']
```

**Numeric/Decimal constraints:**
```dbml
price numeric [note: '{"min": 9.99, "max": 999.99}']
percentage numeric [note: '{"min": 0, "max": 100}']
allocation numeric [note: '{"min": 0.5, "max": 8}']
```

### When to Use Constraints

- **Ratings/Scores**: Always constrain to realistic ranges (1-5, 0-100)
- **Percentages**: Use 0-100 for allocation, completion, etc.
- **Prices**: Set realistic price ranges for your domain
- **Quantities**: Limit inventory, order quantities to reasonable ranges
- **Time periods**: Constrain hours, days, months to valid ranges
- **Business metrics**: API calls, storage limits, user counts

### Examples from This Directory

- **ecommerce.dbml**: Price (5.99-999.99), stock (0-1000), quantity (1-10), rating (1-5)
- **saas_platform.dbml**: Monthly price (0-999), API calls (1K-1M), storage (5-1000 GB), month (1-12)
- **advanced_features.dbml**: Budget (100K-5M), salary (30K-200K), priority (1-5), allocation (0-100%), hours (0.5-8)

---

## Understanding Faker Data Types

All updated examples use **Faker provider names** as column data types. This is a powerful feature of `model2data` that allows you to generate realistic synthetic data:

### Common Faker Data Types

```yaml
# Personal Information
first_name      # John, Mary, Michael, etc.
last_name       # Smith, Johnson, Williams, etc.
email           # john.smith@example.com
phone_number    # +1-555-123-4567
country         # United States, Japan, France, etc.
user_name       # john_smith, mary_jane_99, etc.

# Business/Corporate
company         # Acme Corporation, Global Tech Inc., etc.
slug            # acme-corporation, global-tech, etc.
word            # widget, gadget, service, etc. (good for names)

# Products/Commerce
ean13           # 5901234123457 (product barcode)
isbn13          # 978-3-16-148410-0

# Internet/Tech
ipv4            # 192.168.1.1
url             # https://example.com/page
domain_name     # example.com

# Dates/Times
date            # 2024-01-15
time            # 14:30:45
timestamp       # 2024-01-15 14:30:45

# Identifiers
uuid4           # 550e8400-e29b-41d4-a716-446655440000
md5             # 5d41402abc4b2a76b9719d911017c592
sha1            # 356a192b7913b04c54574d18c28d46e6395428ab
```

### How to Use Faker Types in Your DBML

In your DBML file, simply set the column's `data_type` to a Faker provider name:

```dbml
Table users {
  id bigint [pk, not null]
  email email [unique, not null]           // Uses Faker's email()
  first_name first_name [not null]         // Uses Faker's first_name()
  last_name last_name [not null]           // Uses Faker's last_name()
  phone_number phone_number                // Uses Faker's phone_number()
  company company                          // Uses Faker's company()
}

Table products {
  id bigint [pk, not null]
  name word [not null]                     // Uses Faker's word()
  ean ean13 [unique]                       // Uses Faker's ean13()
  website_url url                          // Uses Faker's url()
  price numeric [not null, note: '{"min": 5.99, "max": 999.99}']  // With constraints
}

Table organizations {
  id bigint [pk, not null]
  name company [not null]                  // Realistic org names
  domain slug [unique]                     // slugified domain names
  created_at timestamp [not null]          // Realistic timestamp
}
```

When you run `model2data generate --file your_schema.dbml --rows 100`, it will:
1. Use Faker to generate realistic values for each column type
2. Respect min/max constraints from inline notes
3. Preserve all foreign key relationships (parent IDs before child IDs)
4. Handle nullability properly (~20% null for optional columns)

### Examples from This Directory

- **ecommerce.dbml**: Uses `email`, `first_name`, `last_name`, `phone_number`, `country`, `word`, `ean13` with price/quantity constraints
- **saas_platform.dbml**: Uses `company`, `slug`, `email`, `first_name`, `last_name`, `word` with subscription/billing constraints
- **advanced_features.dbml**: Uses `first_name`, `last_name`, `email`, `word` with budget/salary/time constraints

---

## Tips for Using These Examples

### 1. **Learn by Doing**
Start with `ecommerce.dbml` — it's the most straightforward and covers common patterns.

### 2. **Run with Different Seed Values**
```bash
# Generate different data distributions
model2data generate --file examples/ecommerce.dbml --rows 100 --seed 1
model2data generate --file examples/ecommerce.dbml --rows 100 --seed 2
model2data generate --file examples/ecommerce.dbml --rows 100 --seed 3
```

### 3. **Vary Row Counts**
```bash
# Small dataset for quick testing
model2data generate --file examples/saas_platform.dbml --rows 10 --seed 42

# Large dataset for performance testing
model2data generate --file examples/saas_platform.dbml --rows 10000 --seed 42
```

### 4. **Inspect Generated Files**
```bash
# Check the generated dbt project structure
ls -la dbt_ecommerce/

# Review the generated staging models
cat dbt_ecommerce/models/ecommerce/staging/stg_customers.sql

# Check seed data
head -5 dbt_ecommerce/seeds/ecommerce/customers.csv
```

### 5. **Modify Examples**
Copy an example and customize it for your own use case:
```bash
cp examples/ecommerce.dbml examples/my_store.dbml
# Edit my_store.dbml with your custom tables and constraints...
model2data generate --file examples/my_store.dbml --rows 100 --seed 42
```

### 6. **Experiment with Constraints**
```bash
# Try different constraint ranges to see their effect
# Edit the note constraints in the DBML file, then regenerate
model2data generate --file examples/ecommerce.dbml --rows 100 --seed 42
```

---

## Real-World Use Cases

1. **Testing dbt Transformations**: Use these to generate test data for your dbt models
2. **Analytics Development**: Build and test BI dashboards without real data
3. **Data Pipeline Testing**: Validate ETL/ELT logic with realistic schemas
4. **API Testing**: Use the generated data to test integrations
5. **Performance Testing**: Generate large datasets to test query performance
6. **Training & Demos**: Show stakeholders how their data can be structured and analyzed
7. **Data Quality Testing**: Validate that constraints are enforced correctly

---

## Contributing New Examples

Have a domain or pattern you'd like to showcase? Create a new `.dbml` file and submit a PR!

Examples should:
- ✓ Showcase a clear use case or domain
- ✓ Include meaningful relationships (2+ tables)
- ✓ Demonstrate one or more advanced features (Faker types, constraints, etc.)
- ✓ Have helpful comments at the top explaining the purpose
- ✓ Use inline note constraints where appropriate for realistic data ranges
- ✓ Include a variety of Faker data types for realistic generation

---

## Questions or Issues?

See the [main README](../README.md) for more information about `model2data` or [open an issue](https://github.com/JB-Analytica/model2data/issues) with questions!
