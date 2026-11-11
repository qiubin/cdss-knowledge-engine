package dev.cdss.sidecar;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import org.kie.api.io.ResourceType;
import org.kie.api.runtime.KieSession;
import org.kie.internal.utils.KieHelper;

/**
 * Stateless Drools matching sidecar.
 *
 * POST /execute  {"drl": "...", "facts": [{"ruleId": "...", "code": "..."}]}
 *   -> 200 {"matched": ["RULE_ID", ...]}
 * GET /health -> 200 {"status":"ok"}
 *
 * Facts carry only *satisfied* atoms, so DRL patterns test presence and
 * {@code not} means "atom absent for that rule".  DRL files import
 * {@link Key} (a real classpath class) instead of declaring it dynamically.
 */
public class Main {

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8090"));
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        ObjectMapper mapper = new ObjectMapper();

        server.createContext("/health", exchange -> respond(exchange, 200, "{\"status\":\"ok\"}"));

        server.createContext("/execute", exchange -> {
            if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                respond(exchange, 405, "{\"error\":\"POST only\"}");
                return;
            }
            try {
                JsonNode payload = mapper.readTree(exchange.getRequestBody());
                String drl = payload.path("drl").asText("");
                List<String> matched = executeDrl(drl, payload.withArray("facts"));
                respond(exchange, 200, mapper.writeValueAsString(Map.of("matched", matched)));
            } catch (Exception e) {
                respond(exchange, 422, mapper.writeValueAsString(
                        Map.of("error", String.valueOf(e.getMessage()))));
            }
        });

        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        System.out.println("drools-sidecar listening on " + port);
    }

    private static List<String> executeDrl(String drl, JsonNode facts) {
        KieHelper helper = new KieHelper();
        helper.addContent(drl, ResourceType.DRL);
        KieSession session = helper.build().newKieSession();
        try {
            List<String> matched = new ArrayList<>();
            session.setGlobal("matched", matched);
            for (JsonNode fact : facts) {
                Key key = new Key(fact.path("ruleId").asText(""), fact.path("code").asText(""));
                session.insert(key);
            }
            session.fireAllRules();
            return matched;
        } finally {
            session.dispose();
        }
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}
