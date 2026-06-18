Improve `arbiter-docker up` recovery when Docker rejects the staging network
subnet by retrying alternate staging subnets and printing actionable recovery
instructions if no automatic subnet works.
