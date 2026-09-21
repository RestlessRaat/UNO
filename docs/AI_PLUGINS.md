# UNO AI plugin API v1

UNO loads trusted, local Python plugins from:

```text
%LOCALAPPDATA%\UnoNoMercy\plugins\<plugin-folder>\plugin.json
```

When `UNO_USER_DIR` is set, the plugin directory is `<UNO_USER_DIR>/plugins`.
Plugins are discovered once at startup. Copy the complete folder, then restart
the game. `examples/plugins/random-plus` is a working example.
Developers may set `UNO_PLUGIN_DIR` to scan a separate plugin collection without
copying it into the user directory.

## Security and dependencies

Plugins execute as trusted Python code in the game process. They are not
sandboxed, so install only code you trust. UNO does not invoke `pip` or fetch
dependencies. A plugin may use the standard library, packages already bundled
with the game, or compatible dependencies shipped inside its own directory.
The packaged game currently uses Python 3.14.

A timed-out plugin result is ignored and that seat falls back to Normal for the
rest of the round. The timeout is cooperative: Python cannot safely kill an
arbitrary in-process thread, so a malicious infinite loop may require a game
restart.

## Manifest

Required fields are `api_version`, `id`, `name`, `version`, and `entry_point`.
The entry point uses `module:function` syntax and must return an object with a
`choose_action(context)` method. IDs are stable lowercase namespaced values;
external plugins may not use the `builtin.*` namespace.

Optional `time_budget_ms` defaults to 2000 and must be between 100 and 10000.
`settings_schema` supports `boolean`, `integer`, `number`, and `enum`. Numeric
settings may define `min`, `max`, and `step`; enum settings define `options`.
Every setting needs a JSON-compatible default.

External manifests may not request omniscient or private information. God is a
privileged built-in policy and its internal interface is not part of this API.

## Policy contract

Import types only from `uno.ai_api`. `DecisionContext.game` is an immutable
public snapshot containing the AI's own hand, public player counts, public
events, and public discard/roulette information. It never contains opponents'
cards, live engine objects, or the real deck order.

Return one of the immutable action objects already present in
`context.legal_actions`. Do not add a player ID: the host assigns the actor and
validates the returned action immediately before applying it. Use
`random.Random(context.random_seed)` for reproducible randomized decisions.

Policy instances are created once per AI seat per round, so they may keep
round-local memory. Instances are discarded on rematch, plugin failure, or room
closure. Persistent state and custom plugin UI are not part of API v1.

## Compatibility

The public API version changes only for incompatible DTO or lifecycle changes.
The game rejects unsupported versions without preventing other plugins from
loading. Replays store actions, plugin identity, version, and settings metadata;
they remain playable after a plugin is removed because replay never reruns AI.
