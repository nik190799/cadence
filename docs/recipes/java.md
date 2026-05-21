---
layout: default
title: Java recipe
---

# Recipe — Java

A starter `cadence.yaml` for a typical Java project (Maven or Gradle).

```yaml
commands:
  format: ["mvn -q spotless:check"]
  lint:   ["mvn -q -DskipTests compile", "mvn -q checkstyle:check"]
  test:   ["mvn -q test"]

boundaries:
  - where: "src/main/java/com/example/features/**"
    forbidden:
      - "src/main/java/com/example/data/sources/**"
      - "src/main/java/com/example/data/repositories/**"
    reason: "Features must read via narrow providers in com.example.app"

  - where: "src/main/java/com/example/data/sources/**"
    forbidden:
      - "src/main/java/com/example/features/**"
      - "src/main/java/com/example/app/**"
    reason: "Data sources are leaf-level"
```

## Notes

- Replace `com/example` with your actual package root
- Gradle equivalents: `./gradlew spotlessCheck`, `./gradlew check`,
  `./gradlew test`
- For multi-module projects, run `/cadence-init` per module or use a
  root config with module-aware globs
- Consider [ArchUnit](https://www.archunit.org/) for in-process
  architecture rules in addition to Cadence's file-pattern boundaries

## Suggested first ADRs

1. **Dependency injection framework** (Spring / Guice / Dagger / none)
2. **Persistence layer** (JPA / JOOQ / JDBC + manual mapping)
3. **REST framework** (Spring MVC / JAX-RS / Micronaut)
