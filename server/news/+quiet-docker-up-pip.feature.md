Improved generated Docker helper startup output by quieting normal pip
wheel-processing chatter and successful pre-start config-check output, adding
`--verbose` to show pip install output when needed, and passing `ARBITER_COLOR`
into the container so explicit Arbiter config-check statuses keep their colors.
