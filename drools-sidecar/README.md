# Drools Matching Sidecar（参考实现）

Python 引擎把每条规则的条件树编译成 DRL（`src/engine/drools/compiler.py`），
把已评估为真的原子条件投影成 fact，本服务用 Drools RETE 批量求值布尔树，
返回命中的 ruleId。结论与溯源仍由 Python 原生层重建，两种模式输出一致。

## 构建与运行

要求 Java 17 + Maven：

    cd drools-sidecar
    mvn -q package
    PORT=8090 java -jar target/drools-sidecar.jar

## 接口

- `GET /health` -> `{"status":"ok"}`
- `POST /execute`，body：`{"drl": "...", "facts": [{"ruleId":"R1","code":"a0"}]}`
  -> `{"matched": ["R1", ...]}`

## 接入

`data/engine.json`：

    {
      "active": "drools",
      "drools": {
        "base_url": "http://127.0.0.1:8090",
        "timeout_seconds": 5,
        "fail_over_native": true
      }
    }

`fail_over_native: true` 时 sidecar 不可达自动回退原生引擎（推理响应
`engine.fallback_reason` 可见原因）。

## 适用范围

加权评分规则（`weighted_group`）、跨阶段 fact 条件、按规则图谱解析的
`evidence` 条件不参与编译，始终由原生引擎求值；因此中医辨证规则在 Drools
模式下仍走原生路径，行为不受影响。
