# Capa de personalización — se queda en este fork

Esta carpeta es nuestra. Se versiona en **`promitierra/memexos`** como parte de
`main`, y **nunca se propone a `liushengyu/memexos`**.

## La regla

`main` de este fork es *nuestra* versión: el proyecto original más lo que
necesitamos para usarlo. Por eso **ningún PR al repo original sale de `main`**.
Un PR desde `main` arrastraría esta carpeta al diff, y eso rompe las dos cosas a
la vez: el mantenedor recibe ruido personal y nosotros exponemos configuración
que no le sirve a nadie.

El flujo correcto:

```sh
git fetch upstream
git checkout -b feat/<tema> upstream/main     # base limpia, sin la capa
# ... commits que sí sirven a cualquiera ...
git push -u origin feat/<tema>
gh pr create --repo liushengyu/memexos --base main \
             --head promitierra:feat/<tema>
```

Si hay que rescatar un commit que está en `main` pero encima de la capa, se trae
con `git cherry-pick <sha>` a una rama nacida de `upstream/main`.

## Qué contiene

| Archivo | Contenido |
| --- | --- |
| `config.toml` | la configuración real de esta instalación: proveedor propio, cadena de modelos por rol, idioma, consola |
| `apply.sh` | la restaura en el volumen del stack y recrea la consola |
| `notes.md` | decisiones, evidencia y trampas que sólo nos importan a nosotros |

`config.toml` **no contiene secretos**: nombra las variables de entorno
(`api_key_env = "OPEN_DESIGN_API_KEY"`), nunca sus valores.

## Qué NO va acá

Si un cambio de código sirve para cualquiera, va en el árbol normal y se
propone desde una rama de tema. Esta capa es para lo que depende de nuestra
máquina, nuestras cuentas, nuestros modelos y nuestro idioma.

La prueba de una línea: si no se lo recomendaríamos al creador del proyecto, va
acá; si le sirve a cualquier usuario, va a un PR.

## Aplicar

```sh
./apply.sh
```

Escribe la config en el volumen con modo `0600` y recrea la consola. Los modelos
y el vault no se tocan.

## Recuperar después de perder el volumen

```sh
cd ~/docker/memexos && make build-branch   # o make build
./local/apply.sh
```

El `memex setup` del primer arranque deja una configuración por defecto; este
script la reemplaza por la nuestra.
