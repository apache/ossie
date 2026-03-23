package org.osi.converter.polaris;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * REST client for the Apache Polaris catalog.
 * <p>
 * Polaris implements the Iceberg REST Catalog specification.
 * This client communicates with the catalog endpoints to list namespaces,
 * tables, and retrieve table metadata (schemas).
 */
public class PolarisClient {

    private final String baseUrl;
    private final String catalog;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private String token;

    /**
     * Create a new Polaris client.
     *
     * @param baseUrl  the Polaris server base URL (e.g., {@code http://localhost:8181})
     * @param catalog  the catalog name to use
     */
    public PolarisClient(String baseUrl, String catalog) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.catalog = catalog;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(30))
                .build();
        this.objectMapper = new ObjectMapper();
    }

    /**
     * Authenticate using OAuth2 client credentials.
     *
     * @param clientId     the client ID
     * @param clientSecret the client secret
     */
    public void authenticate(String clientId, String clientSecret) throws IOException, InterruptedException {
        String body = "grant_type=client_credentials"
                + "&client_id=" + clientId
                + "&client_secret=" + clientSecret
                + "&scope=PRINCIPAL_ROLE:ALL";

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/api/catalog/v1/oauth/tokens"))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IOException("Authentication failed (HTTP " + response.statusCode() + "): " + response.body());
        }

        JsonNode json = objectMapper.readTree(response.body());
        this.token = json.get("access_token").asText();
    }

    /**
     * Set a pre-existing bearer token for authentication.
     */
    public void setToken(String token) {
        this.token = token;
    }

    /**
     * List all namespaces in the catalog.
     *
     * @return list of namespace identifiers (each namespace is a list of name parts)
     */
    public List<List<String>> listNamespaces() throws IOException, InterruptedException {
        JsonNode json = get("/api/catalog/v1/" + catalog + "/namespaces");
        List<List<String>> namespaces = new ArrayList<>();
        JsonNode nsArray = json.get("namespaces");
        if (nsArray != null && nsArray.isArray()) {
            for (JsonNode ns : nsArray) {
                List<String> parts = new ArrayList<>();
                for (JsonNode part : ns) {
                    parts.add(part.asText());
                }
                namespaces.add(parts);
            }
        }
        return namespaces;
    }

    /**
     * List all tables in a namespace.
     *
     * @param namespace the namespace identifier parts
     * @return list of table names
     */
    public List<String> listTables(List<String> namespace) throws IOException, InterruptedException {
        String nsPath = String.join("\u001F", namespace);
        JsonNode json = get("/api/catalog/v1/" + catalog + "/namespaces/" + nsPath + "/tables");
        List<String> tables = new ArrayList<>();
        JsonNode identifiers = json.get("identifiers");
        if (identifiers != null && identifiers.isArray()) {
            for (JsonNode id : identifiers) {
                tables.add(id.get("name").asText());
            }
        }
        return tables;
    }

    /**
     * Load full table metadata including schema.
     *
     * @param namespace the namespace identifier parts
     * @param tableName the table name
     * @return the full table metadata as JSON
     */
    public JsonNode loadTable(List<String> namespace, String tableName) throws IOException, InterruptedException {
        String nsPath = String.join("\u001F", namespace);
        return get("/api/catalog/v1/" + catalog + "/namespaces/" + nsPath + "/tables/" + tableName);
    }

    /**
     * Create a namespace in the catalog.
     *
     * @param namespace  the namespace identifier parts
     * @param properties optional namespace properties (can be null)
     */
    public void createNamespace(List<String> namespace, java.util.Map<String, String> properties)
            throws IOException, InterruptedException {
        ObjectNode body = objectMapper.createObjectNode();
        ArrayNode nsArray = body.putArray("namespace");
        for (String part : namespace) {
            nsArray.add(part);
        }
        if (properties != null && !properties.isEmpty()) {
            ObjectNode props = body.putObject("properties");
            properties.forEach(props::put);
        }
        post("/api/catalog/v1/" + catalog + "/namespaces", body.toString());
    }

    /**
     * Create a table in the catalog.
     *
     * @param namespace the namespace identifier parts
     * @param tableJson the Iceberg table creation request JSON
     */
    public void createTable(List<String> namespace, String tableJson) throws IOException, InterruptedException {
        String nsPath = String.join("\u001F", namespace);
        post("/api/catalog/v1/" + catalog + "/namespaces/" + nsPath + "/tables", tableJson);
    }

    private JsonNode get(String path) throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .header("Accept", "application/json")
                .GET();
        if (token != null) {
            builder.header("Authorization", "Bearer " + token);
        }
        HttpResponse<String> response = httpClient.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IOException("GET " + path + " failed (HTTP " + response.statusCode() + "): " + response.body());
        }
        return objectMapper.readTree(response.body());
    }

    private void post(String path, String body) throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (token != null) {
            builder.header("Authorization", "Bearer " + token);
        }
        HttpResponse<String> response = httpClient.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200 && response.statusCode() != 201) {
            throw new IOException("POST " + path + " failed (HTTP " + response.statusCode() + "): " + response.body());
        }
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public String getCatalog() {
        return catalog;
    }

    public ObjectMapper getObjectMapper() {
        return objectMapper;
    }
}
