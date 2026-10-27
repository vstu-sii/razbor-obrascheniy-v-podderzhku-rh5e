# Мониторинг LAB04

Запуск и защищённый доступ: [окружения](../docs/environments.md).

- Prometheus: агрегаты очереди/HTTP, резервов, policy denials; alerts.yml.
- Loki + Alloy: JSON файлы из отдельного volume, без Docker socket.
- Tempo: HTTP и model.attempt OTLP spans, без промптов/ответов/ключей.
- Alertmanager: TriageSpendAnomaly виден в API/дашборде. Внешняя рассылка не настроена.
- Grafana: 8 panels и три datasource. Anonymous access выключен.

Каждый отказ авторизации/квоты и деградация пишет тип/причину, без actor/token
в Prometheus labels. Логи используют шаблон route вместо пользовательского URL.
Приложение и worker логируют только metadata. Это не хранение customer payload.

Использованы проверенные теги Prometheus 3.15.1, Alertmanager 0.34.1, Grafana
13.2.4, Loki 3.7.9, Alloy 1.21.2 и поддерживаемая maintenance-линия Tempo 2.10.10.
Tempo работает monolithic, без отдельного Kafka. Для distroless readiness probe
добавлен статический BusyBox из официального образа.
Обновления и CVE-аудит не заменяются фактом pin версии; отдельный supply-chain
аудит этой работой не объявляется выполненным.
