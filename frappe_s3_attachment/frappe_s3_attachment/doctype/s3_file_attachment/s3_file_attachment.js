// Copyright (c) 2018, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('S3 File Attachment', {
	refresh: function(frm) {
		if (frm.doc.public_distribution_id && !frm.is_dirty()) {
			frm.add_custom_button(__("Clear CDN Cache"), () => clear_cdn_cache(frm));
		}
	},
	migrate_existing_files: function (frm) {
        frappe.msgprint("Local files getting migrated", "S3 Migration");
        frappe.call({
            method: "frappe_s3_attachment.controller.migrate_existing_files",
            callback: function (data) {
                if (data.message) {
					frappe.msgprint('Upload Successful')
					location.reload(true);
                } else {
                    frappe.msgprint('Retry');
                }
            }
        });
    },
});

function clear_cdn_cache(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Clear CDN Cache"),
		fields: [
			{
				fieldname: "keys",
				fieldtype: "Small Text",
				label: __("Keys or URLs"),
				reqd: 1,
				description: __(
					"One per line: an object key or its full URL under {0}. End a key with * to clear every key with that prefix. This only clears cached copies; it does not delete or change any file.",
					[frm.doc.public_base_url || __("the public base URL")]
				),
			},
		],
		primary_action_label: __("Clear"),
		primary_action(values) {
			frappe.call({
				method: "frappe_s3_attachment.controller.invalidate_public_assets",
				args: { keys: values.keys },
				freeze: true,
				callback(r) {
					if (!r.message) return;
					dialog.hide();
					frappe.msgprint({
						title: __("Cache Clearing Started"),
						indicator: "green",
						message: __(
							"CloudFront invalidation {0} covers {1} path(s). It usually finishes within a few minutes.",
							[r.message.invalidation_id, r.message.paths.length]
						),
					});
				},
			});
		},
	});
	dialog.show();
}
