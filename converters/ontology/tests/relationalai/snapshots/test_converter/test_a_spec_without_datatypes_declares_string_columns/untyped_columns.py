# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from relationalai.semantics import Decimal, Integer, Model, String

m = Model("model")

StoreNr = m.Concept("StoreNr", extends=[Integer])
Region = m.Concept("Region", extends=[String])
SaleNr = m.Concept("SaleNr", extends=[Integer])
Amount = m.Concept("Amount", extends=[Decimal])
Store = m.Concept("Store")
Sale = m.Concept("Sale")
LargeSale = m.Concept("LargeSale")
db_schema_stores = m.Table("DB.SCHEMA.STORES", schema={'STORENR': String, 'REGION': String})
db_schema_sales = m.Table("DB.SCHEMA.SALES", schema={'SALENR': String, 'STORENR': String, 'STOREREGION': String, 'AMOUNT': String})

Store.nr = m.Property(f"{Store} has {StoreNr:n}")
Sale.nr = m.Property(f"{Sale} has {SaleNr:n}")
LargeSale.nr = m.Property(f"{LargeSale} has {SaleNr:n}")
Store.region = m.Property(f"{Store} is in {Region:r}")
Sale.soldAt = m.Property(f"{Sale} was sold at {Store:s}")
Sale.amount = m.Property(f"{Sale} totals {Amount:a}")

Store.identify_by(Store.nr)
Sale.identify_by(Sale.nr)
LargeSale.identify_by(LargeSale.nr)
m.define(Store.new(nr=db_schema_stores.STORENR))
m.where(store := Store.to_identity(nr=db_schema_stores.STORENR, unsafe=True)).define(store.region(Region(db_schema_stores.REGION)))
m.define(Sale.new(nr=db_schema_sales.SALENR))
m.where(sale := Sale.to_identity(nr=db_schema_sales.SALENR, unsafe=True)).define(sale.amount(Amount(db_schema_sales.AMOUNT)))
m.where(sale := Sale.to_identity(nr=db_schema_sales.SALENR, unsafe=True)).define(sale.soldAt(Store(db_schema_sales.STORENR)))
m.define(Store.new(nr=db_schema_sales.STORENR))
m.where(store := Store.to_identity(nr=db_schema_sales.STORENR, unsafe=True)).define(store.region(Region(db_schema_sales.STOREREGION)))
m.where(Sale, Sale.nr, Sale.amount > 1000).define(LargeSale.new(nr=Sale.nr))
