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

from relationalai.semantics import Model, String

m = Model("model")

Store = m.Concept("Store")
Sale = m.Concept("Sale")
palantir_palantir_sale_table = m.Table("PALANTIR.PALANTIR.SALE_TABLE", schema={'SALE_ID': String, 'AMOUNT': String, 'STORE_ID': String})
palantir_palantir_store_table = m.Table("PALANTIR.PALANTIR.STORE_TABLE", schema={'STORE_ID': String, 'STORE_NAME': String})

Store.store_id = m.Property(f"{Store} store_id {String}")
Sale.sale_id = m.Property(f"{Sale} sale_id {String}")
Sale.amount = m.Property(f"{Sale} amount {String}")
Sale.store_id = m.Property(f"{Sale} store_id {String}")
Store.store_name = m.Property(f"{Store} store_name {String}")
Sale.sale_store = m.Property(f"{Sale} sale_store {Store}")

Store.identify_by(Store.store_id)
Sale.identify_by(Sale.sale_id)
m.define(Sale.new(sale_id=palantir_palantir_sale_table.SALE_ID))
m.where(sale := Sale.to_identity(sale_id=palantir_palantir_sale_table.SALE_ID, unsafe=True)).define(sale.amount(palantir_palantir_sale_table.AMOUNT))
m.where(sale := Sale.to_identity(sale_id=palantir_palantir_sale_table.SALE_ID, unsafe=True)).define(sale.store_id(palantir_palantir_sale_table.STORE_ID))
m.where(sale := Sale.to_identity(sale_id=palantir_palantir_sale_table.SALE_ID, unsafe=True), store := Store.lookup(store_id=palantir_palantir_sale_table.STORE_ID)).define(sale.sale_store(store))
m.define(Store.new(store_id=palantir_palantir_store_table.STORE_ID))
m.where(store := Store.to_identity(store_id=palantir_palantir_store_table.STORE_ID, unsafe=True)).define(store.store_name(palantir_palantir_store_table.STORE_NAME))
