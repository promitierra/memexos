# Notas locales

Decisiones y evidencia de esta instalación. Nada de esto es para el proyecto
original: es lo que aprendimos operándolo con nuestro proveedor, nuestros
modelos y nuestro idioma.

## Cadena de modelos

| Rol | Cadena | Motivo |
| --- | --- | --- |
| writer, reviewer | `amr-flash` → `amr` → `deepseek` | eco devolvió **0 páginas** en 2 intentos (3 s, sin tool calls); flash publicó el lote |
| worker, classify, judge | `amr` → `amr-flash` → `deepseek` | eco clasificó 93 s y juzgó bien: ahí está el volumen de tokens |

Costos según la guía del proveedor: Eco 0.27×, Flash 0.5×, el resto 1× o más.
Eco es el más barato y está recomendado para procesamiento por lotes, pero no
sostiene el paso de escritura.

### Problema abierto

Una cadena de fallback **no se activa** cuando el modelo "escribe cero
archivos": `backends._route` decide por el campo `ok`, y un modelo que contesta
en prosa vuelve con `ok=True`. El worktree no lo ve nadie. Arreglarlo bien pide
que el chequeo de "no cambió nada" viva dentro de la ruta de reintento; es un
refactor con sus tests, no un parche.

Mientras tanto: writer en flash, y eco donde sírinde.

## Trampas verificadas

1. **`MEMEX_*` en el entorno gana sobre toda la config.** `MEMEX_LANG=en` dejó
   el idioma clavado en inglés y el selector parecía roto. Los nombres de
   nuestras variables en el stack no deben empezar con `MEMEX_`.
2. **El lock de escritura es un `flock` real.** «Another job holds the write
   lock» significa que hay un job corriendo. Borrar `locks/*.lock` desenlaza el
   inodo y permite dos escritores del vault: nunca hacerlo. Lanzar trabajo a
   mano dentro del contenedor de la consola (`make ingest`).
3. **`memex ingest` sale con código 0 aunque falle.** La verdad está en
   `memex status`.
4. **`asr.model` vacío significa «el primer `ggml-*.bin`»** en
   `~/.local/share/memexos/models`. No hay que editar el TOML.
5. **`npm_config_prefix=$HOME/.local`** es lo que mantiene `qmd` dentro del
   volumen; sin eso cae en `/usr/local` y muere con el contenedor.

## Idioma

`--lang` acepta cualquier pack que exista en `locale/`: `i18n.available()`
escanea el directorio. No hay lista que mantener en el CLI.

`Config.language` sí filtraba contra `("en","zh")` hardcodeado; en la rama
`feat/console-language-switch` eso está corregido y con test.

## Aportes al proyecto original

En la rama, listos para proponer cuando lo decidamos:

- pack `es` completo para la consola (532 claves, placeholders idénticos);
- botón de configuración + selector de idioma reusando el `modal()` existente;
- acción `set-language` que persiste con las mismas garantías que `memex setup`;
- `index()` resolviendo el idioma por request, no una vez al arrancar;
- corrección de `Config.language`.

Fuera de esa rama, y por eso viven acá: la cadena de modelos, las claves, el
idioma elegido y todo lo que depende de nuestra infraestructura.
