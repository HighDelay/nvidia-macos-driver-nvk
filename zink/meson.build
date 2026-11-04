# SPDX-License-Identifier: MIT
libnvk_zink = shared_library(
  'nvk_zink', ['nvk_zink.c', 'nvk_present.c', 'nvk_iosurface.c'],
  include_directories : [inc_include, inc_src, inc_mesa, inc_gallium, inc_gallium_aux, inc_gallium_drivers, inc_vulkan_util, include_directories('../../drivers/zink')],
  gnu_symbol_visibility : 'hidden',
  link_with : [libglapi, libgallium, libmesa],
  c_args : ['-DNVK_ZINK_NVK'],
  dependencies : [idep_nir_headers, idep_vulkan_util_headers, idep_vulkan_wsi_defines, idep_mesautil, idep_xmlconfig, dep_thread, dep_clock, driver_zink,
                  dependency('appleframeworks', modules : ['IOSurface', 'Foundation'])],
  link_args : ['-Wl,-rpath,@loader_path'],
  install : true,
)
executable('zink_sync_probe', 'zink_sync_probe.c',
  include_directories : [inc_include, inc_src],
  dependencies : [idep_mesautil, dep_thread, dep_clock],
  install : false,
)
