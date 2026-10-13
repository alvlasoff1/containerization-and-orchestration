## Часть 1 

podAntiAffinity и topologySpreadConstraints определяют правила по которым scheduler выбирает ноду для заселения пода, первый позволяет задать параметры пода который не должен находится рядом с планируемым, второй дает возможность указать распределение некоторого набора подов (по лейблам), а также возможную разницу в этом распределении. Например поды api и worker можно настроить таким образом чтобы они не оказывались на одной ноде с помощью podAntiAffinity, а через topologySpreadConstraints задать, чтобы разница в количестве подов api на нодах не превышала одного. 

В данном случае, поскольку количество реплик мало, то для равномерного распределения по нодам можно использовать podAntiAffinity.

## Часть 2

Дописали batch в чарт и подняли в кластере.

## Часть 3 — Рекомендации krr

Сначала проверил order path нагрузкой:

```bash
POST /order — 300 запросов
```

Все запросы завершились успешно. Короткая повторная проверка показана на рисунке [part_3_load.png](part_3_load.png).

После этого включил в Prometheus сбор cAdvisor и kube-state-metrics и запустил `krr` для namespace `lab3-control-plane`. Отчёт сохранён в [krr-report.yaml](krr-report.yaml), снимок вывода — [part_3_krr.png](part_3_krr.png).

Рекомендации `krr`:

- `api`: CPU request `10m`, memory request/limit `100Mi`;
- `worker`: CPU request `10m`, memory request/limit `100Mi`;
- `batch`: CPU request `1004m`, memory request/limit `100Mi`.

Для CPU `krr` рекомендует не задавать limit. Для `postgres` отдельной рекомендации нет, потому что он создаётся оператором CloudNativePG и не определяется `krr` как обычный Deployment или StatefulSet.

Исходные значения в chart были `50m/64Mi` для `api` и `worker`. Рекомендации `krr` показали, что для этих сервисов memory request нужно увеличить до `100Mi`, а CPU request снизить до `10m`.

Значения перенесены в `lecture-3-kubernetes-control-plane/shop/values.yaml`. Первая попытка `helm upgrade` завершилась по timeout: на сервере была одна нода, а обязательный `podAntiAffinity` не позволил временно разместить новую ReplicaSet. Это показано на рисунке [part_3_upgrade_blocked.png](part_3_upgrade_blocked.png).

После добавления второй worker-ноды upgrade был завершён успешно. Для rollout также задано `maxUnavailable: 1`, потому что при обязательном anti-affinity старую ReplicaSet нужно сначала уменьшить, чтобы новая реплика смогла занять освободившуюся ноду.

## Часть 4 — Распределение API по нодам

Для `api` используется `podAntiAffinity` с `topologyKey: kubernetes.io/hostname`. Это запрещает размещать две реплики API на одной ноде.

Проверка выполнена командой:

```bash
kubectl get pods -n lab3-control-plane -l app.kubernetes.io/component=api -o wide
```

Результат проверки показан на рисунке [part_4_spread.png](part_4_spread.png). В кластере видны ноды `devops-labs` и `worker-2b`, а реплики `api` работают на разных нодах.

Вторая worker-нода запущена как отдельный k3s-agent контейнер на той же ВМ. Для Kubernetes это отдельная нода с отдельным hostname, но физически обе ноды зависят от одной ВМ.

Таким образом, правило anti-affinity применилось: scheduler не разместил две реплики `api` на одной ноде. При отказе одной Kubernetes-ноды вторая реплика остаётся на другой ноде.

## Часть 5 — Приоритеты, QoS и PDB

В `values.yaml` добавлены два класса приоритета:

- `shop-critical` со значением `100000` для `api` и `postgres`;
- `shop-batch` со значением `100` для фоновой нагрузки `batch`.

Большое значение priority позволяет критичным pod’ам получить преимущество при дефиците ресурсов и вытеснить менее важные pod’ы. `batch` имеет меньший приоритет.

Для `api` и `postgres` requests равны limits:

- `api`: CPU `10m`, memory `100Mi`;
- `postgres`: CPU `100m`, memory `256Mi`.

Поэтому эти pod’ы относятся к QoS `Guaranteed`. Для `batch` requests меньше limits: CPU `100m`/`2`, memory `100Mi`/`256Mi`. Его QoS — `Burstable`.

Добавлены PDB `shop-api` и `shop-postgres-pdb` с `minAvailable: 1`. Для PostgreSQL также остаётся PDB `shop-postgres-primary`, который создаётся оператором CloudNativePG.

После `helm upgrade` `api` имеет priority `100000` и QoS `Guaranteed`, `batch` — priority `100` и QoS `Burstable`, а новый PostgreSQL pod получил priority `100000` и QoS `Guaranteed`. После штатного restart CloudNativePG кластер вернулся в состояние `Cluster in healthy state`.

Результат проверки показан на рисунке [part_5_priority_pdb.png](part_5_priority_pdb.png).

PDB ограничивает добровольные disruption’ы и не запрещает принудительное вытеснение при preemption или отказ ноды. При memory pressure kubelet сначала рассматривает менее защищённые QoS-классы, поэтому `batch` должен пострадать раньше критического пути.
