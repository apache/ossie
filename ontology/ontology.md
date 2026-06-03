# OSI - Ontology Specification

**Version:** 0.2.0.dev0

## Table of Contents

1. [Enumerations](#enumerations)
2. [Ontology](#ontologies)
3. [Ontology mappings](#ontology-mappings)

---

## Enumerations

Standard enumeration values used throughout the specification.

### Concept types

Ontologies distinguish two different kinds of concepts:

| ConceptType | Description |
|---------|-------------|
| `EntityType` | Real-world concept that must be referenced using other information |
| `ValueType` | A datatype with additional semantics |

An entity type is a concept that represents real-world objects that must be referenced using other
information. For instance, a person might be referenced by their social security number or private
e-mail address. In some modeling languages these are called either entities or object types.

A value type is a concept that represents instances of some data type (i.e SQL types like Integer
or String) with additional semantics. For instance, a social-security number is a string or positive
integer that comprises exactly nine digits. In some modeling langauges these are called data types
or domains.

### Built-in concepts

Ontologies come with several built in concepts that can be referred to by name:

| BuiltInConcept | Description |
|---------|-------------|
| `Any` | Most general entity type |
| `Boolean` | Most general boolean value type |
| `Date` | Most general date value type |
| `DateTime` | Most general datetime value type |
| `Decimal` | Most general decimal value type |
| `Float` | Most general floating point value type |
| `Integer` | Most general integer value type |
| `String` | Most general string value type |


### Multiplicities

The allowable multiplicities of relationships defined in the [Relationships](#relationships) section.

| Multiplicity | Description |
|---------|-------------|
| `ManyToOne` | The last role of a relationship is uniquely determined by the other roles |
| `OneToOne` | The relationship is ManyToOne in both directions (only for binary relationships) |

## Ontologies

Ontologies are conceptual models of enterprise data that describe the enterprise in terms
of concepts, relationships, and business rules. This specification represents an ontology
hierarchically, grouping each relationship under the concept that plays its first role.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique name of this specification |
| `description` | string | No | Human-readable description |
| `ai_context` | string/object | No | Additional context for AI tools |
| `ontology` | list | Yes | Concepts and relationships they group that form this ontology |
| `custom_properties` | object | No | Open set of custom properties not covered by this spec (see [Custom properties](#custom-properties)) |

Each component of an ontology defines a concept and a list of relationships where that
concept plays the first role:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `concept` | Concept | Yes | A concept in this ontology |
| `relationships` | list | No | Relationships where this concept plays the first role |

### Concepts

Concepts represent the types of things that have meaning in a business setting, e.g., person, company,
or salary. Every ontology implicitly includes all of the [built-in concepts](#built-in-concepts) and
may refer to them by name without declaring them. 

Concepts have the following schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique name of this concept |
| `type` | ConceptType | Yes | Entity type or value type |
| `description` | string | No | Human-readable description |
| `extends` | list | No | Names of this concept's supertypes |
| `derived_by` | list | No | Expressions that derive this concept's population |
| `identify_by` | list | No | Names of relationships that uniquely reference objects of this concept |
| `requires` | list | No | Expressions that constrain this concept's population |
| `custom_properties` | object | No | Open set of custom properties not covered by this spec (see [Custom properties](#custom-properties)) |

Each concept is either an entity type or a value type.

### Extends

Every user-declared concept extends one or more concepts in the ontology. The new concept
is a subtype of each concept that it extends, and the extended concepts are its supertypes.

Any value type concept must either directly or indirectly extend one of the built-in value
types like `Integer` and `String`.

Entity type concepts can only extend other entity type concepts, and every entity type
implicitly extends the built-in concept `Any`.

This ontology snippet:
```yaml
name: EnterpriseOntology
ontology:
  - concept:
      name: SocialSecurityNr
      type: ValueType
      extends: [Integer]
  - concept:
      name: Employee
      type: EntityType
      extends: [Person]
```
declares two concepts that extend other concepts.

### Relationships

Relationships relate objects of one or more concepts and declare how to verbalize links among
those objects. Relationships have set (as opposed to bag) semantics, and links do not contain
nulls.

Each relationship that is declared under a concept conforms to the following schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Part of the identifier for this relationship |
| `description` | string | No | Human-readable description |
| `multiplicity` | enum | No | Multiplicity constraint |
| `roles` | list | No | List of additional roles in this relationship |
| `derived_by` | list | No | Expressions that derive links of this relationship |
| `requires` | list | No | Expressions that constrain this relationship's population |
| `verbalizes` | list | Yes | Patterns describing how to verbalize links |
| `custom_properties` | object | No | Open set of custom properties not covered by this spec (see [Custom properties](#custom-properties)) |

Each relationship is uniquely identified by a prepending its declared name with that of the containing
concept. For instance, in:

```yaml
ontology:
  - concept:
      name: Person
      type: EntityType
      identify_by: [ nr ]
    relationships:
      - name: nr
        roles:
          - concept: SocialSecurityNr
        verbalizes: [ '{Person} is identified by {SocialSecurityNr}' ]
        multiplicity: OneToOne
      - name: earns
        roles:
          - concept: Salary
        multiplicity: ManyToOne
        verbalizes: [ "{Person} earns {Salary}" ]
      ...
```

the relationship is identified by the string `Person.earns`. This convention naturally supports
expressions that navigate over the links of relationships using the “dot-join” operator in a
manner that is familiar to object-oriented programming languages. This relationship links
`Person` and `Salary` objects and verbalizes each link as “Person earns Salary.” 

#### Roles

Objects play roles in the links of a relationship. If you think of a relationship as a narrow table,
then its links are like rows and its roles are like columns. Each role is played by a concept that
constrains the type of objects that can play that role in any link. In `Person.earns`, `Person` and
`Salary` play the first and second roles respectively.

By convention, the first role of any relationship is played by the concept under which the
relationship is declared. Any additional roles are enumerated in order in the roles list
using this schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `concept` | string | Yes | Name of the concept that plays this role |
| `name` | string | No | Optional role name |
| `custom_properties` | object | No | Open set of custom properties not covered by this spec (see [Custom properties](#custom-properties)) |

For instance, in:

```yaml
ontology:
  - concept:
      name: Person
      type: EntityType
    relationships:
      - name: files_married_joint
        verbalizes: [ "{Person} files married filing joint" ]
      - name: purchased_on
        roles:
          - concept: Vehicle
          - concept: Date
        multiplicity: ManyToOne
        verbalizes: [ "{Person} puchased {Vehicle} on {Date}" ]
```

the unary relationship `Person.files_married_joint` has an empty roles list, while the
ternary relationship `Person.purchased_on` declares two additional roles played by
`Vehicle` and `Date` respectively,

The role player often suffices to distinguish the role within its relationship, but when
the same concept plays more than one role, the user must declare a distinguising name for
any additional role whose player's name does not distinguish it from other roles in
the same relationship. For instance, in:

```yaml
ontology:
  - concept:
      name: Store
      type: EntityType
    relationships:
      - name: ships_to_in_days
        roles:
          - concept: Store
            name: destination
          - concept: NrDays
        multiplicity: ManyToOne
        verbalizes: [ "{Store} ships to {Store:destination} in {NrDays}" ]
```

the role name `destination` distinguishes the second `Store`-playing role from the first in
this relationship.

Expressions that are used to define derived_by rules and requires constraints will refer to
roles by name -- the name defaulting to the concept that plays the role unless an explicit
role name is provided. In any expression that involving links of the `Store.ships_to_in_days`
relationship can then use the variables `Store` and `destination` to refer to objecs that
play these two `Store`-playing roles without ambiguity.

#### Multiplicities

If a relationship comprises more than one role, objects that play the last role could be functionally
dermined by a tuple of objects that play the other roles. This knowledge is declared using a `ManyToOne`
multiplicity constraint. In the examples above, the constraint declares that each person earns at most
one salary and that for each pair of stores, the former ships to the latter in at most one number of
days. For relationships of ternary and higher arity, the multiplicity applies to the n-th role, meaning
the object that plays the n-th role is functionally determined by the tuple of objects that play
the first n-1 roles.

In the special case of a binary relationship, one might declare a `OneToOne` multiplicity, which
indicates the relationship is many-to-one in both directions. For instance, the `Person.nr`
relationship is one-to-one because each person is identified by at most one social security number
and each social security number identifies at most one person.

### Identifying relationships

Entity-type objects cannot be referenced directly but must instead be referenced using one or more
relationships whose use allows to reference an entity type using other kinds of objects. Some modeling
methods distinguish a preferred identifier to use when referencing an entity type. For instance, the
`Person.nr` relationship can be used to reference a person by their social security number; while
the pair of relationships `License.acct` and `License.seat_nr` can be used to reference a license by
its associated account and seat number. These relationships are always binary, and their first role
is always played by the referent concept, i.e., the concept that the relationship is used to reference.
The `identify_by` array allows modelers to list the names of relationships that form the preferred
idnetifier of a concept.

### Derivation expressions

Concepts and relationships may be derived using expressions. Think of a derived concept or 
relationship as a view whose objects or links are derived from those of other concepts or
relationships. For instance:

```yaml
ontology:
  - concept:
      name: Person
      type: EntityType
    relationships:
      - name: parent_of
        roles:
          - concept: Person
            name: "child"
        verbalizes: [ "{Person} is a parent of {Person:child}", "{Person:child} is a child of {Person}" ]
      - name: ancestor_of
        roles:
          - concept: Person
            name: "descendant"
        derived_by:
          - "Person.parent_of(descendant)"
            "Person.ancestor_of.parent_of(descendant)"  
      - name: taxed_at
        roles:
          - concept: TaxRate
        derived_by:
          - "Person.files_single AND Person.earns <= 11925 AND TaxRate == 10.0"
          - "Person.files_married_joint AND Person.earns <= 23850 AND TaxRate == 10.0"
          - ...
```

declares two derived relationships -- `ancestor_of` and `taxed_at`. Each link of `Person.ancestor_of`
relates a person to one of its descendants. The two expressions form the base and recursive cases for
this calculation. In the base case, a `Person` is an ancestor of some `descendant` if that `Person`
is the parent of that descendant. And in the recursive case, a `Person` is an ancestor of some
`descendant` if that `Person` is an ancestor of the parent of that `descendant`. Notice in this
example how role names are used to disambiguate the two `Person` roles in this relationship.

Each link of `Person.taxed_at` links a `Person` object to a `TaxRate` that is derived using
expressions that determine the rate based on the person's filing status and how much they earn.
If, for some person, none of the expressions can be evaluated, then the relationship will
not link that person.

Expressions that derive a relationship are interpreted as rules for constructing the links of the
relationship in the same way that a SQL query is interpreted as a rule for constructing the rows
of a new table. Each expression must therefore reference each role of the relationship, either
explicitly or implicitly. If an expression evaluates to some object then that object will implicitly
play the last role, and the expression must reference each of the other roles explicitly.
If an expression does not evaluate to any object, then it must explicitly reference each role.

A derived concept is one whose population is derived from that of its supertype concepts
using one or more expressions. For instance:

```yaml
ontology:
  - concept:
      name: Employee
      type: EntityType
      extends: [Person]
      derived_by: [ "EXISTS ( Person.earns )" ]
```

declares that the population of Employee is derived from the population of Person by
classifying each Person who earns some salary as a Employee.

### Requires

The requires list contains expressions that give additional semantics to a concept or relationship
by declaring conditions that must hold over their populations. When applied to a concept, each
expression must reference the concept, as in:

```yaml
ontology:
  - concept:
      name: SocialSecurityNr
      type: ValueType
      extends: [Integer]
      requires: [ "0 < SocialSecurityNr", "SocialSecurityNr <= 999999999" ]
```

When applied to a relationship, each expression must reference one or more roles of the
relationship. For instance, in:

```yaml
ontology:
  - concept:
      name: Item
      type: EntityType
    relationships:
      - name: offers_in
        roles:
          - concept: Store
        verbalizes: [ "{Item} is offered for sale in {Store}", "{Store} offers sale of {Item}" ]
      - name: total_sales_in
        roles:
          - concept: Store
          - concept: Amount
        verbalizes: [ "{Item} sold for cumulative {Amount} in {Store}" ]
        requires:
          - "Amount > 0.0"
          - "Item.offers_in(Store)"
```

the first expression requires any value that plays the `Amount` role to be positive while the second
requires any item that has sales in some store to be offered in that store.

### Custom properties

The ontology root, concepts, relationships, and roles may each carry a `custom_properties` object: an
open set of key-value pairs for data that the core spec does not model. This gives tools a place to
preserve information that would otherwise be lost, which is especially useful when importing an ontology
from an external format such as OWL/RDF and round-tripping it back out.

Keys are free-form, and values may be any JSON. When carrying data from a semantic-web source, CURIEs
such as `owl:equivalentClass` or `rdfs:label` are recommended so the origin of each property stays clear.
Tools that do not understand a given property should preserve it as-is. For example, an OWL importer might
map an `owl:Class` to a concept while retaining its IRI and annotations:

```yaml
ontology:
  - concept:
      name: Person
      type: EntityType
      custom_properties:
        iri: "http://xmlns.com/foaf/0.1/Person"
        rdfs:label@en: "Person"
        rdfs:label@fr: "Personne"
        owl:equivalentClass: "https://schema.org/Person"
    relationships:
      - name: knows
        roles:
          - concept: Person
            name: acquaintance
        verbalizes: [ "{Person} knows {Person:acquaintance}" ]
        custom_properties:
          iri: "http://xmlns.com/foaf/0.1/knows"
          owl:inverseOf: "http://xmlns.com/foaf/0.1/knows"
```

#### Language-tagged literals

RDF literals often carry a language tag (for example `rdfs:label` with values `"Person"@en` and
`"Personne"@fr`). The recommended convention is to append the [BCP 47](https://www.rfc-editor.org/info/bcp47)
language tag to the key after an `@`, giving keys of the form `prefix:local@lang`:

```yaml
custom_properties:
  rdfs:label@en: "Person"
  rdfs:label@fr: "Personne"
```

`@` is not a legal character in a CURIE local name, so `prefix:local@lang` parses unambiguously into the
property and its language. This form holds at most one value per language per key. When a property needs
multiple values in the same language, or carries a non-language datatype such as `xsd:date`, use the
[JSON-LD](https://www.w3.org/TR/json-ld11/) value-object form instead, which is lossless:

```yaml
custom_properties:
  rdfs:label:
    - { "@value": "Person", "@language": "en" }
    - { "@value": "Persona", "@language": "en" }
  dcterms:created:
    "@value": "2020-01-01"
    "@type": "xsd:date"
```

## Ontology mappings

Ontology mappings declare how to map the values of fields at the logical level to objects and links
in the ontology. Just as ontologies are partitioned by concept, ontology maps partition into concept
mappings that group by some concept.

### Concept mappings

Each concept mapping declares how to populate a concept with objects and how to populate the relationships
that group under that concept with links. These declarations are formed from patterns of expressions that
reference fields in a logical model that is declared using the OSI core semantic model spec.

Concept mappings have the following schema:

| Field | Type | Required | Description |
|---------------|---------|-----|-------|
| `concept`         | string  | Yes | Names the concept whose part of the ontology is covered by this concept mapping |
| `object_mappings` | list  | if no `link_mappings` | Mappings that populate this concept |
| `link_mappings`   | list  | if no `object_mappings` | Mappings that populate the relationships grouped under this concept |

### Object mappings

An object mapping describes how to map to the objects of some concept using a pattern of SQL
expressions over the fields of one or more datasets.

An object mapping has the following schema:

| Field | Type | Required | Description |
|---------------|---------|-----|-------|
| `concept`     | string  | No | Names the concept being mapped to using this object map |
| `expression`  | string  | if no `referent_mappings` | SQL expression that computes a value from fields |
| `referent_mappings` | list  | if no `expression` | Referent mappings that find entity objects using identifying realtionships |

When the concept is a value type or an entity type with a simple identifier, then an object mapping is just
a SQL expression. For instance, given this ontology snippet:

```yaml
ontology:
  - concept:
    name: SocialSecurityNr
    type: ValueType
    extends: [ Integer ]
    requires: [ "0 < SocialSecurityNr", "SocialSecurityNr <= 999999999" ]
  - concept:
      name: Person
      type: EntityType
      identify_by: [ nr ]
    relationships:
      - name: nr
        roles:
          - concept: SocialSecurityNr
        multiplicity: OneToOne
        verbalizes: [ "{Person} is identified by {SocialSecurityNr}" ]
```

an object mapping that computes `SocialSecurityNumber` values would use a SQL expression to retrieve or
stitch together an integer value and check that the value satisfies the constraints on that concept.

Because `Person` uses a simple identifier -- one that involves one relationship that uses some
value type to uniquely reference the concept -- an object mapping can map to its objects using a
SQL expression that computes the values of its identifying value type (`SocialSecurityNr`) and
then mapping those values to `Person` objects using the declared identifier. The object mapping in:

```yaml
concept_mappings:
  - concept: Person
    object_mappings:
      - expression: PERSONS.SSN                  
  ...
```
maps values from the `SSN` field of dataset `PERSONS` into `Person` objects.

When an entity-type concept does not provide a simple identifier, the object mapping uses
an array of referent mappings, each of which declares how to use one of the concept's
identifying relationships to map to its objects from other objects.

Referent mappings have the following schema:

| Field | Type | Required | Description |
|----------------|--------|-----|-------|
| `relationship` | string | Yes | Name of an identifying relationship |
| `expression`  | string  | if no `referent_mappings` | SQL expression that computes a value from fields |
| `referent_mappings` | list  | if no `expression` | Referent mappings that find entity objects using identifying realtionships |

For instance, consider this ontology snippet:

```yaml
ontology:
  - concept:
      name: OrderLineItem
      type: EntityType
      identify_by: [ "nr", "order" ]
      requires: [ "OrderLineItem.nr", "OrderLineItem.order" ]
    relationships:
      - name: nr
        roles: [ concept: LineNr ]
        multiplicity: ManyToOne
      - name: order
        roles: [ concept: CustOrder ]
        multiplicity: ManyToOne
```

and notice that `OrderLineItem` has a compound identifier. This concept mapping:

```yaml
concept_mappings:
  - concept: OrderLineItem
    object_mappings:
      - referent_mappings:
          - relationship: nr
            expression: LINEITEMS.L_LINENUMBER
          - relationship: order
            referent_mappings:
              - relationship: CustOrder.nr
                expression: LINEITEMS.L_ORDERKEY
```

contains a single object mapping that uses two referent mappings, which use:
1. an expression to map the `L_LINENUMBER` field to `LineNr` values to provide to the `nr` relationship, and
2. a nested referent mapping that maps to `Order` objects to provide to the `order` relationship.
The nested referent mapping is needed because `Order` is an entity type.

#### Link mappings

A concept mapping's link mappings describe how to map logical field schema to the relationships that
group under the concept associated with the mapping.

These mappings are organized into tree structures to avoid duplication and clarify mapping intent in
the typical case when fields map to objects that play roles in many different relationships. Semantically,
each link mapping uses a pattern of SQL expressions to map to object tuples, which can then form the links
of some relationship or can form the prefixes of longer tuples that are mapped to by the mapping's
children.

Link mappings have the following schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `object_mapping` | object | Yes | Maps to objects in the last position of mapped tuples |
| `relationship` | string | No | Relationship whose links include the tuples mapped to by this mapping |
| `children` | list | No | List of child nodes in the tree |

The level of a link mapping must coincide with the arity of the relationship it names. So a
top-level mapping could name a unary relationship, a mapping at level 2 could name to a binary
relationship, and so forth.

For instance, this ontology snippet:

```yaml
ontology:
  - concept:
      name: Item
      type: EntityType
      identify_by: [ nr ]
    relationships:
      - name: nr
        roles: [ concept: SkuNr ]
        multiplicity: OneToOne
        verbalizes: "{Item} is identified by {SkuNr}"
      - name: active     # A unary relationship
        verbalizes: [ "{Item} is actively sold" ]
      - name: active_in
        roles: [ concept: Store ]
        verbalizes: [ "{Item} is actively sold in {Store}" ]
      - name: returned_in_for
        roles: [ concept: Store, concept: Amount ]
        verbalizes: [ "{Item} returned in {Store} for {Amount}" ]
        multiplicity: ManyToOne
      - name: sold_in_for
        roles: [ concept: Store, concept: Amount ]
        verbalizes: [ "{Item} sells in {Store} for {Amount}" ]
        multiplicity: ManyToOne
```
declares one unary, one binary, and two ternary relationships whose links would be tuples
of the form (Item), (Item, Store), and (Item, Store, Amount) respectively. And suppose a
logical model declares a dataset called `METRICS` with fields like `SKU`, `STORE`, `SALES`,
and `RETURNS` among many others. This link mapping populates the relationships using these
fields:

```yaml
    concept_mappings:
      - concept: Item
        link_mappings:
          - object_mapping:
              referent_mappings:
                relationship: Item.nr
                expression: METRICS.SKU
            relationship: active
            children:
              - object_mapping:
                  concept: Store
                  expression: METRICS.STORE
                relationship: active_in
                children:
                  - object_mapping:
                      concept: Amount
                      expression: METRICS.SALES
                    relationship: sold_in_for
                  - object_mapping:
                      concept: Amount
                      expression: METRICS.RETURNS
                    relationship: returned_in_for
```
The top level mapping is a tree with one root node, one node at level 2, and two nodes at
level 3. Each node maps fields of the `METRICS` dataset to links of four different relationships,
and notice how the mapping to `Item` objects is declared once even though `Item` plays a role in
all four of the relationships and that the mapping to `Store` objects is declared once even
though `Store` plays a role in three of the relationships.

## Version History

- **0.2.0.dev0** (2026-05-29): Basic support for ontologies and logical schema mappings
  - Core ontology structure: Concepts, relationships, and business rules (requires and derived_by)
  - Schema mappings from one or more logical models into an ontology
  - `custom_properties` on the ontology root, concepts, relationships, and roles for carrying
    data not covered by the core spec (e.g. when importing from OWL/RDF)

---

## License

See LICENSE file for details.
