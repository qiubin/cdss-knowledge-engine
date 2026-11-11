package dev.cdss.sidecar;

/**
 * Atom fact: one satisfied leaf condition of one rule.  The Python compiler
 * emits {@code import dev.cdss.sidecar.Key} so DRL patterns match this class
 * directly; facts are inserted for satisfied atoms only, so LHS patterns test
 * presence and {@code not} means "atom absent for that rule".
 */
public class Key {

    private String ruleId;
    private String code;

    public Key() {
    }

    public Key(String ruleId, String code) {
        this.ruleId = ruleId;
        this.code = code;
    }

    public String getRuleId() { return ruleId; }
    public void setRuleId(String ruleId) { this.ruleId = ruleId; }
    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }
}
