BBB compatibility
=================

.. list-table:: Catalog support
   :header-rows: 1

   * - BBB version
     - Catalog
     - Status
   * - 3.0
     - Bundled and generated during every release build
     - Supported
   * - 2.7
     - Generate from a BBB 2.7 source checkout using ``SBC_BBB_SCHEMA``
     - Source-compatible catalog support
   * - Future versions
     - Generate from that release's ``bbb-graphql-schema.md``
     - Source-compatible catalog support

The generated catalog preserves every table and scalar field found in the given
source schema. Deployment-specific permissions and optional plugins still depend
on the connected BBB server.
